# MemoryBridge privacy notice

MemoryBridge is self-hosted infrastructure. By default, the MemoryBridge open-source project and its contributors do not receive, operate, or centrally collect an operator's memories. Data flows only to the systems the operator configures, such as its MemoryBridge server, Qdrant instance, embedding provider, archive storage, authorization server, and connected AI clients.

A deployment operator is responsible for deciding what data may be stored, who may access it, retention/backup policy, applicable privacy notices and legal obligations, and the security of all configured infrastructure.

MemoryBridge clients should not persist passwords, API keys, bearer tokens, private keys, one-time authentication codes, session cookies, payment authorization data, or hidden model reasoning. Operators should use least-privilege authorization, per-user/per-device access controls where available, encrypted transport, and protected backups.

Deleting or correcting data is an operator responsibility using the operator-controlled storage/API procedures appropriate to the deployment. MemoryBridge's general MCP surface intentionally does not expose an unrestricted deletion tool.

This notice describes the open-source software. A third party that hosts MemoryBridge for others must provide its own privacy notice describing that service's actual data practices.
