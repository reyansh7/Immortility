# Python edits

- Prefer the smallest change that satisfies the request.
- Read a file before editing it.
- Keep imports, public names, and call sites consistent.
- After Python edits, syntax-check touched files (`py_compile`).
- Run only targeted pytest paths from the plan — never the whole suite unless asked.
- Do not invent files outside the project root.
