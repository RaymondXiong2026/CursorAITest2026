import argparse
from pathlib import Path

import httpx


def chunk_text(text: str, chunk_size: int = 300) -> list[str]:
    text = text.strip()
    if not text:
        return []
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest sample KB text into RAG API")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="RAG API base URL")
    parser.add_argument("--doc-id", default="sample-doc-001", help="Document id")
    parser.add_argument("--source", default="sample.txt", help="Source label")
    parser.add_argument("file", help="Text file path")
    args = parser.parse_args()

    file_path = Path(args.file)
    text = file_path.read_text(encoding="utf-8")
    chunks = chunk_text(text)
    payload = {
        "doc_id": args.doc_id,
        "chunks": [
            {
                "content": c,
                "source": args.source,
                "lang": "zh-cn",
                "metadata": {"script": "ingest_sample"},
            }
            for c in chunks
        ],
    }

    with httpx.Client(timeout=30) as client:
        resp = client.post(f"{args.api}/kb/ingest", json=payload)
        resp.raise_for_status()
        print(resp.json())


if __name__ == "__main__":
    main()
