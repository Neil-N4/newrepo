# Voice Right Hackathon Repo

This repo contains the current Voice Right hackathon app scaffold:

- `index.html`: frontend UI
- `server.ts`: Bun server for the app shell and profile/memory flow
- `brain.py`: profile memory and reasoning helpers
- `stt.py`: dual-STT pipeline entrypoint
- `router.py`: hybrid local/cloud routing
- `actions.py`: action execution helpers
- `compose_route.py`: Flask blueprint version of the compose pipeline

## Notes

- Cactus is expected to be available locally on the machine.
- Runtime secrets should go in `.env`, not `.env.example`.
- Browser audio is expected as WebM and should be converted to WAV before STT.
