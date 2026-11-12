# Beta Earth releases

## Find the latest Windows game

The [itch.io download page](https://zappytap.itch.io/beta-earth-sovereignty-next) lists both Windows downloads and their versions. The [Steam store](https://store.steampowered.com/app/5114130/) offers the full game. The source sample in this repository remains a separate edition.

## HUD Flow update on itch.io

The November 11, 2026 storefront check confirmed **0.90.0-hudflow.1** for the full Windows game and **0.90.0-hudflow.1-demo.1** for the free Last Signal demo. Combat has a dedicated pane for enemy tells, strikes, and target assessments. The World Feed keeps exploration and story consequences in view. Players can pin or collapse the Combat pane, or leave it on Auto so it opens during a fight.

To try the update, choose **Last Signal** on the [itch.io download page](https://zappytap.itch.io/beta-earth-sovereignty-next), extract the complete Windows ZIP, and run `BetaEarthSovereignty.bat`. Watch the Combat pane during an encounter, then use the World Feed to follow what happened next. The [HUD Flow release note](https://zappytap.itch.io/beta-earth-sovereignty-next/devlog/1764930/read-the-fight-stay-in-the-world) shows the change and links to the downloads.

The November 9 Windows release checks reached character entry in each edition. On November 11, both public itch.io ZIPs were downloaded and compared with the saved releases. Every file path and SHA-256 matched, including both manifests. The downloads have their own ZIP-level checksums. Use itch.io for the Last Signal download.

## Previous Windows launch update on itch.io

The November 8, 2026 storefront check found **0.90.0-guidance.3** for the full Windows game and **0.90.0-guidance.3-demo.1** for Last Signal. The [November 5 launch update](https://zappytap.itch.io/beta-earth-sovereignty-next/devlog/1756356/windows-launch-fix) addresses the blank browser menu that could appear at startup. If the menu stays blank, open the address shown in the launcher.

For a short play session, choose **Last Signal**, extract the complete ZIP into a writable local folder, and run `BetaEarthSovereignty.bat`. Choose a mode and follow character creation. The demo keeps its own saves. Use a separate folder when updating, and keep a backup of existing saves.

The November 8 check confirmed the public listing and version labels. The November 5 release record separately covers Windows launch checks and platform delivery. Later development candidates have their own verification records.

## Recorded Windows release: 0.90.0-dev.18 — October 7, 2026

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

The previous package combined the same two adventures and introduced an optional scene for the chosen class, with pause, resume, skip, and start-without-training controls. It remains a historical release identity, separate from the recorded dev.18 package.

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
