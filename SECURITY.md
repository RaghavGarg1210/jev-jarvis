# Security and local data

Jev-Jarvis is a personal loopback service with a small capability allowlist. It is not a sandbox against malicious software already running as your user, and it is not designed for public hosting or multiple users.

## Boundaries

- Every execution needs a stored, unexpired, single-use plan. A confirmation cannot supply new action arguments.
- The complete plan is validated before the first effect. A failed step stops a routine; prior steps are not silently rolled back.
- Native calls use fixed argument lists without a shell. AppleScript source is fixed and text passes as arguments. Only configured app names, exact contacts, and configured Shortcuts are available.
- Notes use generated names, exclusive creation, and directory-relative operations. Undo verifies that a generated note is still the same regular file with identical content. Arbitrary file deletion is not a capability.
- The server only binds `127.0.0.1`. Host/Origin checks and mutation tokens reduce cross-site request attacks. Never expose it via a tunnel or reverse proxy.
- Credentials remain server-side. Jev calls go to the fixed TypeSafe endpoint without redirects. Local Ollama uses a fixed loopback endpoint. No provider silently falls back to a cloud model.

## Known limits

Models can misunderstand requests. Review destinations, text, and every step; confidence scores are not security decisions. Configured Shortcuts can do anything you put in them, and Jarvis only displays your description of those effects. Local apps and the browser may use their own network services.

An operating-system timeout can leave an uncertain outcome. Check the affected app before trying again, especially Messages. A macOS success is acceptance of the command, not proof of delivery. If the process stops during execution, the next start labels the receipt interrupted; it does not rerun the plan.

Activity contains titles, results, local note paths, and internal undo metadata. It omits the full request and message body, but is still personal plain-text data. Notes contain exactly the text you chose to save. State defaults to `~/.jev-jarvis/`, with private permissions on newly created files/directories. Keep backups and local access controls appropriate for your machine.

Do not put secrets in prompts, notes, app aliases, or Shortcut names. Rehearsal prevents action effects but still calls whichever decision/planning provider you selected. Hosted Jev receives the request text even in rehearsal.

## Reporting

Please avoid posting exploit details, credentials, or private data in a public issue. Use GitHub's private vulnerability reporting for this repository if enabled; otherwise open an issue asking for a private contact without including the exploit or personal data.
