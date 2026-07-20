# Threat model 0.9.0

The protected assets are event history, encrypted import sources, projections,
keys, backups, release contracts and UI code. The attacker may modify local
files or input archives but is not assumed to control the running OS kernel or
an already unlocked user session. Residual risk includes host compromise,
physical key extraction and loss of every independent recovery key.

| Threat | Effect | Detection | Defense | Recovery | Required test |
|---|---|---|---|---|---|
| Manipulated CSV | false transactions or parser abuse | strict profile and row validation | immutable raw hash, bounded parser, no formula execution | reject batch and retain prior state | malformed and oversized rows |
| Corrupt snapshot | missing or changed history | authenticated decryption, SQLite and event-hash checks | encrypted atomic snapshots | restore a verified complete backup | ciphertext and event tampering |
| Wrong or lost key | unreadable store | stable decryption/key status errors | OS Keychain and independent archive key | restore archive after key reprovisioning; no bypass | wrong-key startup |
| Manipulated backup | hostile or false restore | Fernet authentication, file hashes, schema checks | verify fully before staging | keep current store unchanged | bit flip and manifest mismatch |
| Downgrade | old code corrupts new data | version comparison | fail-closed store/archive minimums | install compatible signed release | future schema/archive |
| Incomplete migration | mixed schemas | migration log and schema version | in-memory transaction and encrypted preimage retention | reopen previous snapshot or restore backup | injected migration abort |
| Process abort | temporary or stale artifacts | sibling lock and temp names | atomic rename, explicit stale recovery | inspect owner and recover exact stale instance | stale PID and owner mismatch |
| Disk full | truncated persistence/restore | free-space startup check and write failures | stage beside target, atomic replace | free space and retry from intact store | insufficient-space state |
| Parallel processes | lost updates | PID/start/instance lock record | exclusive `O_EXCL` single writer | close owner; explicit stale recovery | two simultaneous writers |
| Symlink/traversal | escape trusted local root | component and archive-member checks | reject symlinks, absolute/`..` archive paths | choose a direct local path | filesystem and ZIP symlinks |
| Archive bomb | memory/disk exhaustion | compressed/uncompressed limits and ratio | member, count, total and payload bounds | reject without extraction | high-ratio member |
| Compromised UI/schema | misleading values or contract bypass | embedded tree hashes and signature | blocking startup state | reinstall signed artifact | mutate UI and schema |

Financial correctness remains event-sourced: rules never rewrite historical
events, user confirmations remain authoritative, and all projections can be
rebuilt. This does not protect against a fully compromised operating system;
that residual risk must be handled with OS patching, disk encryption and
physical access controls.
