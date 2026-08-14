# Operation Playbook

Use this reference only after the local userbot registry has no matching module. First inspect the exact installed Telethon constructor with `scripts/telethon_api_inventory.py`, then open the official URL it prints.

## Profile, identity and privacy

| Need | API family | Verification |
|---|---|---|
| Read own profile | `client.get_me()`, `users.GetFullUserRequest` | Read-only |
| Name, bio, username | `account.UpdateProfileRequest`, `account.UpdateUsernameRequest` | Re-read profile |
| Profile photo | `client.upload_file()` + `photos.UploadProfilePhotoRequest` / update/delete photo request | Inspect returned/read-back photo |
| Emoji status | `account.UpdateEmojiStatusRequest(types.EmojiStatus(id))` | Re-read status; resolve document first |
| Channel emoji status | `channels.UpdateEmojiStatusRequest` | Re-read channel state |
| Privacy / notifications | `account.Get/SetPrivacyRequest`, `Get/UpdateNotifySettingsRequest` | Re-fetch exact setting |
| Active sessions | `account.GetAuthorizationsRequest`, web equivalent | Read-only by default |

Do not generic-automate passwords, phone changes, recovery, passkeys, logout/reset-all, account deletion, or payment-adjacent account operations.

## Messages and media

Prefer high-level `TelegramClient` methods when they cover the operation:

| Need | Preferred method | Guard |
|---|---|---|
| Search/history | `iter_messages`, `get_messages` | finite limit; compact previews |
| Send text/file/album | `send_message`, `send_file` | preview target/content then exact returned-message read-back |
| Edit | `edit_message` | only intended/outgoing messages; `MessageNotModifiedError` is no-op only after read-back |
| Delete | `delete_messages(..., revoke=True)` | frozen IDs and post-audit |
| Forward | `forward_messages` | source + destination preview; forwarding is external disclosure |
| Reaction | `messages.SendReactionRequest` | preserve account’s existing reactions |
| Pin | `pin_message`, `unpin_message` | inspect exact pin state and re-fetch after |
| Download | `download_media` | exact IDs only; contained local destination, no overwrite by default |
| Draft/poll/schedule | `messages.SaveDraftRequest`, `SendVoteRequest`, scheduled `send_message` | explicit state/target/time confirmation |

## Inline custom emoji and reactions

These are not sticker-pack operations.

### Inline custom emoji in text or caption

```python
text = '<tg-emoji emoji-id="<document_id>">visible text</tg-emoji>'
await client.send_message(peer, text, parse_mode="html")
```

The inner text must be non-empty. Read the message back and inspect entities if verification needs to distinguish a custom emoji from plain text.

### Custom emoji reaction

Use `types.ReactionCustomEmoji(document_id)` inside the account’s complete desired reaction list passed to `messages.SendReactionRequest`. Sending only a new reaction can replace existing account reactions.

## Groups, channels, forums and invites

| Need | API family | Guard |
|---|---|---|
| Title/about/photo | `channels.EditTitleRequest`, `EditPhotoRequest`; legacy `messages.EditChat*Request` | re-read group/channel |
| Members/permissions | `iter_participants`, `get_permissions` | bounded inventory first |
| Admin rights | `edit_admin` / `channels.EditAdminRequest` | render complete rights set before write |
| Restrict/ban/kick | `edit_permissions`, `channels.EditBannedRequest`, `kick_participant` | target is non-self; finite restriction unless explicit permanent approval |
| Invite / remove | `InviteToChannelRequest`, relevant `EditBannedRequest` | confirm recipient/target exactly |
| Discussion group | `channels.SetDiscussionGroupRequest` | type-check both resolved peers |
| Signatures / slow mode / joins | `channels.Toggle*Request` | group configuration is high impact; re-fetch state |
| Forum topics | `ToggleForumRequest`, `messages.Create/EditForumTopicRequest` | verify forum peer + topic id |
| Invite links | `messages.Export/Edit/DeleteExportedChatInviteRequest` | show expiry, usage limit and target before write |
| Admin log | `channels.GetAdminLogRequest` | read-only, permission limited |

## Contacts, folders and chat lists

- **Single contact:** `contacts.AddContactRequest`, dry-run and never share own phone by default.
- **Bulk contacts:** `contacts.ImportContactsRequest` only via a dedicated CSV module with normalization, dedupe, masked samples, finite batches and progress persistence.
- **Block/unblock:** `contacts.BlockRequest` / `UnblockRequest`, then read back block state.
- **Folders:** `folders.EditPeerFoldersRequest`; plan every peer/folder assignment first.
- **Chat-list invites:** `chatlists` export/edit/join/leave requests; membership/access changes need explicit confirmation.

## Sticker sets and custom emoji packs

| Lifecycle step | Request | Required verification |
|---|---|---|
| Create pack | `stickers.CreateStickerSetRequest(..., emojis=True)` | title, unique short name, emoji flag, count |
| Add | `AddStickerToSetRequest` | count before/after + source document present |
| Replace | `ReplaceStickerRequest` | expected item/position and final count |
| Change keywords/emoji | `ChangeStickerRequest` | re-fetch set |
| Reorder | `ChangeStickerPositionRequest` | re-fetch positions |
| Remove/delete | `RemoveStickerFromSetRequest`, `DeleteStickerSetRequest` | absence or revised count |

A pack item requires a real `InputDocument` with id, access hash, and file reference. Upload/resolve it; never manufacture those values.

## Stories

- Read: `stories.GetPeerStoriesRequest`, `GetStoriesByIDRequest`, archive/views requests.
- Write: `stories.SendStoryRequest`, `EditStoryRequest`, `DeleteStoriesRequest`, `TogglePinnedRequest`, reaction request.
- Story publishing is public/external. Preview peer, privacy, media, caption and expiry; then verify returned story IDs.

## Error policy

- `FloodWaitError`: wait `seconds + 1`, retry the same unit once.
- `MessageNotModifiedError`: idempotent only if final state already matches.
- `MessageEditTimeExpiredError`, `ChatWriteForbiddenError`, `ChatAdminRequiredError`, `UserAdminInvalidError`: stop that unit. Never turn an edit failure into deletion without explicit fallback instruction.
- `ChannelPrivateError`, `MessageIdInvalidError`: re-resolve target/ID; do not guess another peer.
- `PackShortNameOccupiedError`: choose a new approved name; do not claim creation succeeded.
