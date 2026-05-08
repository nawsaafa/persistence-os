# persistence-os examples

Runnable demos of substrate features. Each script is self-contained and
imports from `persistence.*` (install editable: `pip install -e .` from repo
root).

## demo_audit_tamper.py — Mimir Phase B

Builds a 3-call audit chain signed with Ed25519, verifies it with the
public key, flips one character of a signature, and shows `verify_chain`
returns `False`.

```
$ python examples/demo_audit_tamper.py
Built signed audit chain of 3 entries.
Signer: ed25519-pub:9b74624e4bd54778

verify_chain(clean)    -> True

Flipped one character at position 13 of entries[1].signature.
verify_chain(tampered) -> False  <- tamper detected
```

A third party who holds only the public key can verify what the agent
did, without trusting the runtime that produced the chain.

### Screencast — 90s talk track

**Setup (off-camera):**
- Editor on the left half showing `examples/demo_audit_tamper.py`.
- Terminal on the right half in `~/Projects/persistence-os` with the
  `.venv` activated. Confirm the script runs cleanly first.
- 1080p / 30fps / mp4 / under 10MB.

**0:00–0:15 — hook (15s).** Camera on the editor, code visible.
> Every Claude Code agent today logs what it did. Almost none of them
> *prove* it. This is persistence-os — a substrate where every agent
> action lands on a hash-chained audit log. With Mimir Phase B, every
> entry is also Ed25519-signed, so a third party can verify what your
> AI actually did without trusting your runtime.

**0:15–0:35 — code walkthrough (20s).** Cursor on the source.
1. Highlight `generate_keypair()` — fresh Ed25519 keypair.
2. Highlight `make_audit_handler(..., signer=(signer_id, priv))` — every
   entry signed.
3. Highlight the three `perform(":llm/call", ...)` calls.
4. Highlight `verify_chain(entries, public_keys={signer_id: pub})`.

> That's the entire integration: one kwarg on the handler, one kwarg on
> verify.

**0:35–1:05 — run + tamper (30s).** Click into the terminal.

```
python examples/demo_audit_tamper.py
```

> Three signed entries, one byte flipped, chain rejects. The runtime
> that produced the chain doesn't have to be trusted — only the public
> key does.

**1:05–1:30 — close (25s).**
> This is the foundation Mimir's supervisor uses to sign every spawn,
> read, write, and halt with the fleet root key. It's open-core under
> AGPL-3 at github.com/nawsaafa/persistence-os. The
> `persistence-orchestrate` Skill ships with v0.9.0a1 in June. That's
> persistence-os — the substrate that makes your agents accountable.

**Post-record:**
- Trim to under 95s.
- Upload: YouTube (unlisted) → LinkedIn build-in-public post → X thread
  → HN Show submission → cold-DM thread (10 named people).
- All within ~24h after demo lands, per ADR-008 distribution wave.
