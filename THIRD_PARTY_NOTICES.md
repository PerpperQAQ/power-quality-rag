# Third-party notices

The root MIT license covers the original code and public documentation in this repository. It does not relicense third-party dependencies, trademarks, source materials, or upstream reference material. This repository distributes source code; dependencies are installed separately.

## PyMuPDF / MuPDF

`backend/pyproject.toml` requires PyMuPDF, and `backend/app/parsing.py` imports it at module load. It is currently a required dependency, even if you do not parse PDF files.

PyMuPDF and MuPDF are available under **AGPL and commercial licenses**. Installing or using the integrated backend does not mean that only MIT obligations apply. Redistribution or network service use may require compliance with the AGPL, including applicable source availability obligations, or an appropriate commercial license. No commercial license is included or claimed by this project.

- [Official licensing information](https://pymupdf.io/licensing)
- [PyMuPDF license and copyright](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)
- [GNU AGPL v3](https://www.gnu.org/licenses/agpl-3.0.html)

The private development environment's optional PyMuPDF add-ons are not bundled or declared as additional requirements in this public snapshot.

## RAGFlow reference catalog

`backend/app/tongyi_models.json` records model names and capability metadata based on a RAGFlow model catalog reference. The source links remain in that file. The local metadata is adapted with project-specific availability, grouping and capability notes; it is a dated reference, not a current service guarantee or endorsement.

- Upstream: [infiniflow/ragflow](https://github.com/infiniflow/ragflow)
- Reference: [conf/llm_factories.json](https://github.com/infiniflow/ragflow/blob/main/conf/llm_factories.json)
- Upstream license: Apache License 2.0; a copy is included in `licenses/RAGFlow-Apache-2.0.txt`.

Any upstream-derived portions retain their original license and attribution. Model and provider names belong to their respective owners. No model weights are included.

## Installed packages

The frontend dependencies are declared in `package.json` and locked in `package-lock.json`. Backend dependencies are declared in `backend/pyproject.toml`. Each package retains its own license; inspect the exact versions you install before redistributing an application or service. In particular, Python database drivers and PDF dependencies do not all use MIT.

The source snapshot includes no vendored npm/Python packages, third-party PDFs, fonts, screenshots, customer documents, or private knowledge corpus. Demonstration text in the frontend is sample content and does not represent a real company policy or an authoritative power-quality reference.
