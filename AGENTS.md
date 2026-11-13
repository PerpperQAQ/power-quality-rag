# Contributor guidance

- Read README.md and THIRD_PARTY_NOTICES.md before changing startup or distribution.
- This is a development snapshot. Keep frontend demo results, offline tests, live model tests and business acceptance distinct.
- Keep credentials, encrypted credential stores, customer documents, source corpora, snapshots, databases, local paths and execution evidence out of commits and logs.
- Use isolated data directories and empty disposable databases for tests. Never delete user data or connect tests to an existing installation.
- External model, embedding, reranking, OCR and search requests require explicit authorization and limits. Do not run paid calls as part of ordinary build checks.
- Preserve loopback binding and local authentication. A frontend role selector is not trusted identity.
- Run the relevant public checks documented in README.md. Do not claim omitted private-data tests have passed.
