# Finuchet Bot — Repository Status

## Current baseline
- Repository prepared for clean Git-based workflow.
- Sensitive/runtime files are excluded via `.gitignore`.
- No secrets, database dumps, or backup artifacts are tracked.

## Delivery workflow
All further changes should follow:
1. Create branch (`feature/<short-name>` or `fix/<short-name>`).
2. Open PR.
3. Review and merge.
4. Pull merged changes on VPS and restart `finuchet.service`.

## Deployment target
- Server: Ubuntu VPS
- App path: `/root/bot_finuchet`
- Service: `finuchet.service`
