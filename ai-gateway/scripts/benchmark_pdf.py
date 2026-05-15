import time
from pathlib import Path
from app.services.ingestion_service import parse_pdf
path = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'benchmark.pdf'
started = time.perf_counter(); parse_pdf(path); elapsed = time.perf_counter() - started
print({'file': str(path), 'seconds': round(elapsed, 3)})
