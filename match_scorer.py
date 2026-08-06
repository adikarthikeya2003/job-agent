"""
Match scoring: resume sections vs job description, 0-100.

Weighting (see config.yaml -> scoring.section_weights), default:
  skills                0.40  -- highest weight: tightest keyword signal, lowest noise
  experience_projects   0.40  -- keyword + semantic overlap on bullets, second highest
  summary               0.10  -- low weight on purpose: often generic/boilerplate,
                                  must never be the sole driver of a score
  education              0.10  -- lowest weight: rarely differentiates candidates for JD fit

Each section gets its own similarity score against the JD text, then a weighted sum
produces the final 0-100. If a section is empty (e.g. no summary present), its weight
is redistributed proportionally across the remaining non-empty sections so the score
still reflects 100% of available signal rather than being deflated by a missing section.

Two backends:
  - tfidf: sklearn TfidfVectorizer + cosine similarity. Zero network cost, deterministic,
    fast. Weak on synonyms/paraphrase (e.g. "LLM orchestration" vs "agent orchestration"
    won't match without shared tokens) -- this is why low-end scores can look noisy: a
    resume that's a genuine semantic fit but uses different vocabulary than the JD will
    score lower than it "should." TF-IDF is the default because it's free, needs no
    model download, and is fully deterministic/auditable (every point traces to a shared
    token).
  - embeddings: local sentence-transformers (all-MiniLM-L6-v2), captures paraphrase and
    synonym overlap, better recall on true fits, but scores are less directly explainable
    (a cosine similarity between dense vectors) and the first run needs a one-time model
    download (still free, no API key, runs entirely offline after that).
"""
import re
from dataclasses import dataclass
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.#-]{1,}")

_STOPWORDS = set("""a an the and or of to in on for with at by from as is are was were be
been being this that these those you your our we they it its our ours their his her will
must have has had do does did not no can may should would could job role team work years
experience required responsibilities requirements about company""".split())


def _tokenize(text: str) -> list:
    return [t.lower() for t in _TOKEN_RE.findall(text) if t.lower() not in _STOPWORDS]


@dataclass
class SectionScore:
    section: str
    score: float          # 0-100
    weight_used: float     # after redistribution
    top_keywords: list


class MatchScorer:
    def __init__(self, method: str = "tfidf", embeddings_model: str = "all-MiniLM-L6-v2",
                 section_weights: dict | None = None, score_boost: float = 1.0):
        self.method = method
        self._st_model = None
        self._embeddings_model_name = embeddings_model
        self.section_weights = section_weights or {
            "skills": 0.40, "experience_projects": 0.40, "summary": 0.10, "education": 0.10,
        }
        # Raw cosine similarity between a resume section and a full JD (which is mostly
        # company boilerplate, benefits copy, and EEO text) rarely exceeds ~0.15 even for
        # a genuinely strong match -- the shared keywords are a small fraction of total
        # tokens on both sides. score_boost is a documented, fixed linear multiplier
        # applied to raw_similarity*100 so scores land in a usable 0-100 range; it does
        # NOT change which postings rank above others (same ordering, just rescaled), and
        # every point is still traceable to the same keyword/embedding overlap as before.
        # Tune via config.yaml scoring.score_boost if your score distribution runs
        # persistently high or low against your own surface_threshold.
        self.score_boost = score_boost

    def _get_st_model(self):
        if self._st_model is None:
            from sentence_transformers import SentenceTransformer
            self._st_model = SentenceTransformer(self._embeddings_model_name)
        return self._st_model

    def _tfidf_similarity(self, section_text: str, jd_text: str):
        corpus = [section_text, jd_text]
        vec = TfidfVectorizer(tokenizer=_tokenize, lowercase=False, token_pattern=None)
        try:
            matrix = vec.fit_transform(corpus)
        except ValueError:
            return 0.0, []
        sim = float(cosine_similarity(matrix[0:1], matrix[1:2])[0][0])
        # top overlapping keywords driving the score, for traceability
        section_tokens = set(_tokenize(section_text))
        jd_tokens = set(_tokenize(jd_text))
        overlap = sorted(section_tokens & jd_tokens)[:12]
        return sim * 100.0 * self.score_boost, overlap

    def _embedding_similarity(self, section_text: str, jd_text: str):
        model = self._get_st_model()
        embs = model.encode([section_text, jd_text])
        sim = float(cosine_similarity([embs[0]], [embs[1]])[0][0])
        section_tokens = set(_tokenize(section_text))
        jd_tokens = set(_tokenize(jd_text))
        overlap = sorted(section_tokens & jd_tokens)[:12]
        return max(sim, 0.0) * 100.0 * self.score_boost, overlap

    def _section_similarity(self, section_text: str, jd_text: str):
        if not section_text.strip():
            return 0.0, []
        if self.method == "embeddings":
            return self._embedding_similarity(section_text, jd_text)
        return self._tfidf_similarity(section_text, jd_text)

    def _tfidf_similarity_batch(self, section_text: str, jd_texts: list):
        """Fits one TF-IDF vectorizer across [section_text] + all JDs in the batch, so IDF
        reflects real term rarity across many postings instead of just a single pair (a
        2-document fit makes IDF nearly meaningless and systematically deflates scores).
        Returns a list of (score_0_100, overlap_keywords) aligned to jd_texts."""
        corpus = [section_text] + jd_texts
        vec = TfidfVectorizer(tokenizer=_tokenize, lowercase=False, token_pattern=None,
                               min_df=1)
        try:
            matrix = vec.fit_transform(corpus)
        except ValueError:
            return [(0.0, []) for _ in jd_texts]
        sims = cosine_similarity(matrix[0:1], matrix[1:])[0]
        section_tokens = set(_tokenize(section_text))
        results = []
        for i, jd_text in enumerate(jd_texts):
            jd_tokens = set(_tokenize(jd_text))
            overlap = sorted(section_tokens & jd_tokens)[:12]
            results.append((float(sims[i]) * 100.0 * self.score_boost, overlap))
        return results

    def score_batch(self, sections: dict, jd_texts: list) -> list:
        """Scores one resume against many JDs at once (e.g. all postings from one company
        fetch), fitting TF-IDF jointly across the batch for meaningful IDF weighting.
        Returns a list of the same shape as score()'s return, one per jd_text."""
        present = {name: text for name, text in sections.items() if text and text.strip()}
        if not present:
            raise ValueError("Resume has no parseable sections — cannot score.")
        base_weight_sum = sum(self.section_weights.get(n, 0) for n in present)
        if base_weight_sum == 0:
            redistributed = {n: 1.0 / len(present) for n in present}
        else:
            redistributed = {n: self.section_weights.get(n, 0) / base_weight_sum for n in present}

        n = len(jd_texts)
        totals = [0.0] * n
        breakdowns = [[] for _ in range(n)]
        keyword_lists = [[] for _ in range(n)]

        for name, text in present.items():
            w = redistributed[name]
            if self.method == "embeddings":
                per_jd = [self._embedding_similarity(text, jd) for jd in jd_texts]
            else:
                per_jd = self._tfidf_similarity_batch(text, jd_texts)
            for i, (sim_score, keywords) in enumerate(per_jd):
                totals[i] += sim_score * w
                breakdowns[i].append(SectionScore(section=name, score=round(sim_score, 1),
                                                   weight_used=round(w, 3), top_keywords=keywords))
                keyword_lists[i].extend(keywords)

        results = []
        for i in range(n):
            top_keywords = list(dict.fromkeys(keyword_lists[i]))[:15]
            results.append({
                "total": round(min(totals[i], 100.0), 1),
                "breakdown": breakdowns[i],
                "top_keywords": top_keywords,
            })
        return results

    def score(self, sections: dict, jd_text: str) -> dict:
        """sections: {skills, experience_projects, summary, education} -> plain text.
        Returns {"total": float, "breakdown": [SectionScore, ...], "top_keywords": [...]}.
        """
        present = {name: text for name, text in sections.items() if text and text.strip()}
        if not present:
            raise ValueError("Resume has no parseable sections — cannot score.")

        # Redistribute weight of missing sections proportionally across present ones.
        base_weight_sum = sum(self.section_weights.get(n, 0) for n in present)
        if base_weight_sum == 0:
            # no configured weights matched present sections; split evenly
            redistributed = {n: 1.0 / len(present) for n in present}
        else:
            redistributed = {
                n: self.section_weights.get(n, 0) / base_weight_sum for n in present
            }

        breakdown = []
        total = 0.0
        all_keywords = []
        for name, text in present.items():
            sim_score, keywords = self._section_similarity(text, jd_text)
            w = redistributed[name]
            total += sim_score * w
            breakdown.append(SectionScore(section=name, score=round(sim_score, 1),
                                           weight_used=round(w, 3), top_keywords=keywords))
            all_keywords.extend(keywords)

        top_keywords = list(dict.fromkeys(all_keywords))[:15]
        return {
            "total": round(min(total, 100.0), 1),
            "breakdown": breakdown,
            "top_keywords": top_keywords,
        }
