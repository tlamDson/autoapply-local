"""Job-description keywords and resume coverage, with no model call.

The vocabulary is a fixed list of common technology terms. A job's keywords are
the vocabulary terms its description names; the score of a resume is the share
of those keywords the resume text also names. It only breaks ties between
resumes that already match the role tag; it never decides facts.
"""

import re
from functools import lru_cache

_TERMS = """
python java javascript typescript golang rust c++ c# .net scala kotlin swift ruby php matlab sql bash
react angular vue next.js node.js django flask fastapi graphql restful grpc microservices
postgresql postgres mysql mongodb redis dynamodb cassandra elasticsearch kafka rabbitmq spark hadoop
airflow snowflake databricks aws azure gcp lambda s3 ec2 docker kubernetes terraform ansible jenkins
ci/cd linux unix git pytorch tensorflow scikit-learn pandas numpy opencv llm rag
langchain openai embeddings nlp etl agile scrum jira html css tailwind ios android embedded firmware
rtos
"""
_PHRASES = (
    "google cloud", "react native", "vector database", "computer vision", "machine learning",
    "deep learning", "data pipeline", "distributed systems", "unit testing", "integration testing",
    "test automation", "rest api", "spring boot", "github actions",
)
VOCABULARY = frozenset(_TERMS.split()) | frozenset(_PHRASES)


@lru_cache(maxsize=None)
def _pattern(term):
    body = r"[\s-]+".join(re.escape(part) for part in term.split())
    return re.compile(rf"(?<![a-z0-9+#.]){body}(?![a-z0-9+#])")


def _has(term, text):
    return _pattern(term).search(text) is not None


def keywords(text):
    """Sorted vocabulary terms named in text (whole tokens, case-insensitive)."""
    lowered = str(text or "").casefold()
    return sorted(term for term in VOCABULARY if _has(term, lowered))


def coverage(jd_text, resume_text):
    """Share of the job's keywords found in the resume, or None when the job names none."""
    wanted = keywords(jd_text)
    if not wanted:
        return None
    resume = str(resume_text or "").casefold()
    return round(sum(1 for term in wanted if _has(term, resume)) / len(wanted), 3)
