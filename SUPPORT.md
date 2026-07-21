# Support boundaries for 1.1.0

## Supported runtime

| Area | Supported boundary |
|---|---|
| Platforms | macOS arm64/x86_64; Linux x86_64 with a desktop Secret Service |
| Python | CPython 3.11, 3.12, 3.13 and 3.14 |
| Keychains | macOS Keychain; Secret-Service-compatible Linux keyring through `keyring` |
| Store schemas | 1 and 2 are migration inputs; 3 is the writable current version |
| Event schema | 1.0.0 |
| Contract package | 1.2.0 |
| Import profiles | `GenericFinanceCsvV1`; `GermanMultiAccountCsvV1` |
| Migration entry | product 0.2.0 through 1.0.0 |
| Tested CSV size | 10,000 rows in one synthetic import |
| Tested encrypted archive | at least 512 KiB, fully created and verified |
| Configured archive hard limit | 512 MiB encrypted payload; 2 GiB total uncompressed members |

The release gate is executed on macOS arm64 with CPython 3.14. The other listed
Python/platform combinations are supported by the pinned pure-Python package
surface and binary dependency wheels, but should be repeated in platform CI
before distributing a platform-specific desktop wrapper.

## Deliberate limitations

- no import profile other than `GenericFinanceCsvV1` and the explicitly structured `GermanMultiAccountCsvV1`;
- no bank API, browser banking or public HTTP API;
- no cloud synchronization or network storage;
- no external AI, local generative model or OCR in the core MVP;
- no tax calculation, receipts or tax export;
- no investment advice or automated trading;
- no multi-user or shared-workspace operation;
- no partial backup restore;
- no cryptographic recovery after loss of every applicable store/archive key.

Windows and mobile platforms are not part of the 1.1.0 support statement. The
desktop host itself remains a separate packaging concern; the included React UI
and Python service have no remote runtime resources.

## Semantic versioning after 1.0.0

- `1.1.x`: bug fixes, security updates and internal optimizations without
  additive contract changes.
- `1.x.0`: backward-compatible functionality and additive contracts.
- `2.0.0`: incompatible command, event, store or query changes.

Product, store, event, policy and contract versions remain separate.
