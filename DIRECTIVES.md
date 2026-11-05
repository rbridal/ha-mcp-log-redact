# ha-mcp-log-redact — Directives

Keep HA-MCP secret paths and webhook ids out of Home Assistant logs (length-preserving redaction).

## Ownership
- **Masta-Rob** owns this home project DIRECTIVES.md. Flag Rob only for policy changes.

## Safety (always wins)
- Log masking must keep the same length (standing estate rule).
- Never log raw secrets while "testing" redaction.

## Directives (priority order, highest first)
- Prefer length-preserving masks so log alignment/debugging still works.
- Cover secret-path control URLs and webhook ids used by dash/HA.

## Non-goals
- Not a general PII scrubber for all HA logs.

## Standing rules that apply here
- No secrets in git. Length-preserving masking rule.

## Change log
- 2026-11-04 — Masta-Rob — Initial DIRECTIVES.md (Rob's OK: yes, pt1410u).
