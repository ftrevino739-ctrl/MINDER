# MINDER — Security review and Windows acceptance gate

## Status

This is an engineering review of safeguards and regression coverage, not a security certification or proof that the application is production-ready. GitHub Actions can verify Python syntax and mocked behavior; it cannot validate the target Windows desktop, real accounts, installed applications, audio devices, or native dialogs.

The repository's current CI workflow checks Python 3.11, 3.12 and 3.13 and runs the provider regression suite. Do not treat a green run as a substitute for the Windows checklist below.

## Safeguards covered by code and regression tests

- **Human approval:** irreversible actions use the HUD confirmation queue. If the HUD is unavailable, an irreversible action fails closed. A model-supplied `confirmed` argument is not an authorization token.
- **Messages:** sending requires human approval of the exact platform, recipient and complete message. The preview must fit in the approval panel; otherwise sending is refused.
- **Browser:** address navigation is limited to HTTP(S). Local and active URI schemes are blocked. Sensitive controls, form submissions, unknown page actions and risky navigation require approval. Edited form values and the page/control state are rechecked after approval; passwords are redacted. Search actions remain available without a confirmation prompt.
- **Desktop input:** opaque screen clicks, drags, risky shortcuts, clearing/replacing a focused field, Enter and Delete require human approval. The resulting state is reported as unverified unless it is independently observed.
- **System settings:** shutdown, restart and Wi-Fi toggles require HUD approval. The Linux XRandR brightness fallback uses argument-vector subprocess calls rather than shell expansion.
- **Files:** new writes use exclusive creation where implemented; overwrite of large or non-UTF-8 files requires approval and rechecking. Delete moves a reviewed target to the operating-system Trash / Recycle Bin, rechecks the target fingerprint, and refuses changed targets and symbolic links. Desktop organization is previewed and approved before moves. Copy/move destination collisions are refused; undo is guarded against later user changes.
- **Code and archives:** Code Helper and DevAgent restrict project/file access to approved user workspaces, show replacement/run/dependency commands for approval where implemented, avoid shell execution for reviewed commands, and reject symbolic-link targets. Archive extraction rejects traversal, absolute paths and unsafe entries, applies size/count limits, previews the plan and rechecks the archive before extraction. Derived output paths are created without intentional overwrites.
- **Game updater:** install, update, create/replace a daily schedule and cancel a schedule require approval. The approval shows the platform, target, schedule time and optional automatic-shutdown behavior. MINDER does not choose a Steam profile, choose a library drive or click Install; the person completes the native Steam install dialog.
- **Undo and reporting:** undo callbacks compare current files/trees with the version MINDER created or moved, so later user edits are not silently deleted. Actions should report failure or an unverified result rather than claim success without evidence.

## Important limitations

- This repository currently has a plugin template but no configured third-party plugin integrations in `plugins/`. Any future plugin must receive its own external-side-effect and credential review before being enabled.
- Reminder creation schedules an operating-system notification task after the user explicitly requests it. The generated reminder script is stored under the user's home directory and removes itself after firing.
- Some reversible file actions (such as a small UTF-8 text overwrite) use an in-memory undo snapshot rather than a durable backup. Closing or crashing the app can remove that undo opportunity.
- Platform-specific code exists for Windows, macOS and Linux, but this project is Windows-first. The non-Windows paths have not been validated on corresponding physical systems.
- Mocks do not demonstrate that Windows Task Scheduler, the Windows shell, Playwright's installed browsers, real profile locks, microphone permissions, speaker output, hotkeys, window focus, clipboard access or hardware-specific brightness controls behave correctly.
- Human confirmation lowers risk but does not make arbitrary websites, generated code, downloaded packages or third-party services trustworthy. Review package names, commands and content before approving them.

## Windows acceptance checklist

Use a disposable test folder and dummy data. Do not run this checklist against important documents, live accounts or payment pages.

1. **Startup and setup:** install in a clean Python 3.12 virtual environment, launch `main.py`, complete first-run provider setup, close MINDER and relaunch.
2. **Voice:** verify the selected microphone receives speech, transcription is correct, interruption works, and the selected voice plays through the expected output device.
3. **Application control:** open a harmless installed application, bring an existing window forward, then test closing only a disposable window. Confirm MINDER does not report success when the app did not open.
4. **Browser:** open a normal HTTPS page, search, follow a safe link, test a `mailto:` or other non-HTTP link is blocked, edit a dummy form, and verify navigation prompts. Change the draft while approval is pending and verify the stale approval is refused.
5. **Desktop controls:** try a safe screen action, an unknown target, a field replacement and Enter/Delete. Confirm and cancel separately; inspect the active window before approving.
6. **Files:** create a disposable folder and sample files. Test create, write, copy, move, rename, organize and delete. Confirm each visible approval, test cancel, change a file while approval is pending, and verify the stale action is refused. Test undo after editing the moved/copied output.
7. **Code and archives:** use a disposable workspace. Test a no-overwrite project creation, inspect an exact dependency-install prompt without approving unknown packages, run a harmless script, and extract a benign test archive. Do not test malicious archives on important data.
8. **Game updater:** use a free/small test title only if appropriate. Confirm the approval appears before Steam/Epic update actions; verify account, library and Install choices remain manual. Do not enable automatic shutdown during acceptance.
9. **Messaging:** only use a dummy/test account and a recipient who has agreed to receive a test. Verify the complete message is visible before confirmation. Cancel at least once.
10. **Reminder:** set a reminder a few minutes ahead and verify one notification. Confirm the scheduled task belongs to MINDER; do not edit or delete unrelated scheduler tasks.

Record the Windows version, Python version, selected provider/model, browser, microphone, speaker and each checklist result. Report failures with the exact error text and relevant log excerpt.

## Release gate

MINDER is **not 100% verified** until the Windows acceptance checklist has been completed on the target PC and any failures have been fixed and retested. Do not label it production-ready based only on CI.
