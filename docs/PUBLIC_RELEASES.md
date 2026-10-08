# Beta Earth releases

## Current experimental Windows game: 0.90.0-dev.18

[Download Beta Earth: Sovereignty Next](https://zappytap.itch.io/beta-earth-sovereignty-next).

The experimental Windows package combines Sovereignty and Second Chances behind one launcher, with separate characters, progression and saves. Optional class guidance, clearer targeting hints, and Hold/Resume controls help players follow the story and combat. The Digital Library is available from the mode menu. The package includes the runtime for normal offline solo play; newer implementation files are not distributed in this repository.

### Inspect or try it

Download the combined Windows ZIP from the linked game page, extract the complete archive into a writable local folder, and run `BetaEarthSovereignty.bat`. Choose a mode and follow character creation. Keep the launcher open while playing; use **Save & return to modes** to switch adventures. The download page shows supplementary lore artwork, labels it separately from gameplay, and provides requirements and content disclosures.

Close the game and back up the entire installed folder before updating. Extract the new release separately and preserve the complete runtime folder. Older builds using save format 4 cannot read upgraded saves; returning to them requires a verified pre-update backup. Older standalone saves are not automatically converted.

### Release identity and evidence

The October 7, 2026 publication record identifies:

- Version: `0.90.0-dev.18`.
- Build: `BESOV-FOUNDATION-HUD-EXPERIMENTAL18`.
- Archive: `BetaEarth_Combined_0.90.0-dev.18_Windows_Player.zip`.
- Size: 340,094,685 bytes.
- SHA-256: `200c2d86c9e26027daeab6c4add1712e7847674a2c63a8eeda7af1db6a77a3cc`.

That publication record reports an independently downloaded public ZIP with a matching checksum, clean-install integrity, startup checks for both modes, and a local support export. The October 7 showcase review separately checked the retained exact archive, all member CRCs and all 931 root-managed payload hashes, and confirmed the public listing. It scanned readable text for defined privacy patterns; binary assets were not comprehensively inspected. This showcase pass did not redownload the public ZIP or run the game.

This remains an experimental release. Complete later Second Chances progression, every quest branch, human balance and full accessibility review are not established. Private co-op is experimental: connections across computers and routers remain unverified and may fail. The itch.io edition does not include Steam achievements or Steam Cloud. The publication record does not establish a public Steam release or verified Steam client launch, unlocks or Cloud restoration. Windows 10 compatibility remains unverified. Automatic crash capture is not guaranteed; a dedicated local support export is available.

## Previous Windows release: 0.90.0-dev.17

The previous package combined the same two adventures and introduced an optional scene for the chosen class, with pause, resume, skip, and start-without-training controls. It remains a historical release identity, separate from the current dev.18 package.

The October 4, 2026 publication record identifies:

- Version: `0.90.0-dev.17`.
- Build: `BESOV-ONE-SCENE-TRAINING-EXPERIMENTAL`.
- Archive: `BetaEarth_Combined_0.90.0-dev.17_Windows_Player.zip`.
- Size: 341,418,510 bytes.
- SHA-256: `7afde477457ee32ff582513d95b10e99a2de0cb6cfdb1d6929719e4b55ca99b0`.

The October 4 publication record reports a matching independent public-download checksum, final preflight, startup checks for both modes, and a local support export. An earlier October 7 showcase review verified the retained archive's checksum, CRC, and all 926 root-managed payload hashes and confirmed the then-current dev.17 public file listing. That review did not redownload the public Windows ZIP or run the game. Broad gameplay, device, and live Steam-feature tests were not rerun; earlier test results are not fresh qualification of this exact artifact.

The game remains in development. Automatic crash-report capture can occasionally be unavailable; a dedicated local support export is provided separately. Keep backups of saves before updating. The download page provides current installation requirements, content notes, and AI-assisted asset disclosures. Windows 10 compatibility remains unverified. This record does not establish a public Steam release or verified live achievement unlocks or Cloud restoration.

## Source-visible evaluation build: 0.51.1

The code in this repository remains `0.51.1`, build `BESOV-0.51.1-20260816-HUD-TRUTH-ALIGNMENT`. Its [setup instructions](../README.md#run-the-source-sample-locally), [system requirements](../SYSTEM_REQUIREMENTS.md), tests, and managed-file manifest apply to that edition only.

The source sample supports review of combat scheduling, save recovery, domain boundaries, and an accessible loopback browser interface. It is not a substitute for the current packaged game. Its [evaluation terms](../LICENSE.md) remain unchanged.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
