"""Import a reviewed local evidence file, with screenshots; no HTTP or credentials."""

import argparse
import hashlib
import json
from pathlib import Path

from hotradar.contents import BrowserSample, import_sample
from hotradar.db.session import session_factory

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("file", type=Path)
args = parser.parse_args()
samples = [
    BrowserSample.model_validate(record) for record in json.loads(args.file.read_text("utf-8"))
]
for sample in samples:
    evidence = (root / sample.evidence_file).resolve()
    if not evidence.is_relative_to(root / "docs/content-sources/screens"):
        raise ValueError("Screenshot is outside the evidence directory.")
    if hashlib.sha256(evidence.read_bytes()).hexdigest() != sample.evidence_sha256:
        raise ValueError("Screenshot evidence hash does not match.")
with session_factory().begin() as session:
    for sample in samples:
        row, created = import_sample(session, sample)
        print(json.dumps({"id": row.id, "source": row.source_id, "created": created}))
