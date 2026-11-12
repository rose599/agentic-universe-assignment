# AI Usage Disclosure

## Tools used
- Claude
- ChatGPT

## What I used them for
- Claude: Generated the initial implementation for Assignment A.
- ChatGPT: Assisted with understanding the assignment, environment setup, debugging, test execution, and validation of the ingestion and reporting pipeline.

## What I wrote or decided myself
I selected Assignment A, set up and ran the project locally, executed the supplied data generator, ran both ingestion stages, inspected the generated reports, and verified that re-ingesting an already processed drop did not change report totals. I reviewed and tested the implementation rather than relying only on generated output.

## Where the AI was wrong or unhelpful
The initial solution files were placed outside the expected `solution` Python package, causing import errors when running the tests. I moved the modules into the correct package and resolved the test-path issue. I then reran the test suite successfully.

## How I verified AI-generated code
- Ran the test suite: 105 tests passed.
- Generated the supplied small dataset using seed 42.
- Successfully ingested both data drops.
- Generated and inspected all five required reports.
- Re-ingested the first drop and regenerated the reports.
- Confirmed that the data-quality report totals remained unchanged after re-ingestion.
