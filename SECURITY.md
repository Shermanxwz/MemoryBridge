# Security

- Keep Qdrant on localhost/private networking. Do not expose port 6333 to the public Internet.
- Put MemoryBridge behind HTTPS. Remote MCP should use a per-device bearer token or an external OAuth 2.1 AS.
- `MEMORYBRIDGE_BEARER_TOKENS` is a bootstrap/static-token mode intended for a single trusted operator. Rotate a
  leaked device token immediately.
- Use separate New API credentials with only the model permissions required for embedding.
- CloudDrive2/archive paths contain conversations and should be encrypted/permission-restricted at rest.
- Restore is intentionally never automatic or destructive: `restore-latest` refuses an existing collection unless
  `--force` is supplied and verifies the SHA-256 manifest first.
