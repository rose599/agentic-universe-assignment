# Submission

## Candidate
- Name: Heena Hashmi
- Assignment chosen: A 
- Time spent (honest estimate): - Time spent (honest estimate): 9 hours
## How to run

Run these commands from the repository root using Python 3.11 or later.

```powershell
# Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
python -m pip install -r requirements.txt

# Generate sample input data
python .\assignment-a-data\tools\generate_data.py --seed 42 --scale small --out data

# Ingest both data drops
python -m solution ingest .\data\drop_1
python -m solution ingest .\data\drop_2

# Generate reports
python -m solution report --out .\reports

# Run automated tests
python -m pytest tests -q
```

The reporting command generates five files:

- `batch_summary.csv`
- `daily_overview.csv`
- `lead_report.csv`
- `rejections.csv`
- `data_quality.json`

## Design

The solution is organized into separate modules to keep the data pipeline maintainable and responsibilities clear.

- Ingestion: `ingest.py` handles the process of bringing input data into the pipeline.
- Normalization: `normalize.py` handles data normalization before further processing.
- Database: `db.py` manages database connectivity and persistence.
- Events: `events.py` handles event-related operations.
- Data handling: `schemas.py` and `leads.py` contain supporting schema and lead-processing logic.
- Reporting: `report.py` is responsible for generating reports.
- Security: `security.py` provides security-related functionality.
- CLI: `cli.py` and `__main__.py` provide the command-line entry point.

The modular structure separates ingestion, processing, persistence, reporting, and security concerns. This makes individual components easier to test, maintain, and extend.

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| Separate the solution into modules | Improves maintainability and separation of concerns | Keeping all logic in one file |
| Normalize input data before processing | Helps maintain consistent data representation | Processing raw input directly |
| Keep database operations separate | Makes persistence logic easier to manage and test | Mixing database queries into pipeline logic |
| Separate reporting from ingestion | Allows reporting logic to evolve independently | Generating reports during ingestion |
| Include a dedicated security module | Keeps security-related functionality isolated | Handling security throughout unrelated modules |

## Failure modes considered

Potential failure scenarios include:

- Invalid or malformed input data.
- Missing or unexpected fields.
- Duplicate records and repeated processing.
- Database connection or persistence errors.
- Failures during individual pipeline stages.
- Unexpected input formats.

The automated test suite validates the implemented behavior across 105 tests.

Further work could include more extensive testing of process termination, recovery after crashes, very large datasets, and concurrent ingestion. These should be treated as areas for further validation rather than assumed to be fully handled.

## Testing

The automated test suite was executed using:

```powershell
python -m pytest tests -q
```

Result: 105 passed in 21.29 seconds.

No test failures or errors remained in this test run.

The tests were run locally using Python 3.11.9 and an activated virtual environment.

## Results

- Assignment A implementation completed.
- Both supplied data drops ingested successfully.
- All five required reports generated.
- Automated test suite: 105 passed.
- Total test execution time: 21.29 seconds.
- Re-ingestion of an already processed drop was verified to leave report totals unchanged.

The sample run recorded:
- 2,057 lead rows processed.
- 108 rejected lead rows.
- 57 duplicate lead rows merged.
- 8,745 event lines processed.
- 237 duplicate events dropped.
- 8 malformed events.
- 462 orphan events remaining.
- 18 unknown disposition events.

A separate full-pipeline performance benchmark was not recorded during this test run.

## If I had another day

1. Improve observability: Add structured logging and clearer error reporting to make pipeline failures easier to diagnose.
2. Performance testing: Benchmark the pipeline with larger datasets and optimize database queries and ingestion where necessary.
3. Strengthen failure recovery: Test interruption and restart scenarios to validate consistency and safe reruns.
4. Expand test coverage: Add more edge cases for malformed input, missing fields, duplicate records, and unexpected data formats.
5. Improve documentation: Add architecture diagrams, example CLI commands, and troubleshooting guidance.