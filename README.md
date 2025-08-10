# Slack Agent with AgentCore Gateway & Identity

Strands Agents SDK × AgentCore RuntimeとAgentCore Gateway, Identityを使用したSlack統合エージェントの実装です。AWS Bedrock AgentCore RuntimeでホストされるAIエージェントが、セキュアな認証を経由してSlackワークスペースとやり取りを行います。

具体的な実装方法及び詳細な解説は[Bedrock AgentCore GatewayとIdentityを使ってSlackへアクセスしてみる - Qiita](https://qiita.com/har1101/items/aae967fa157b01e414a9)にて解説しているので、こちらをご参照ください。

## アーキテクチャ概要

### コンポーネント構成

![alt text](<architecture.png>)

### 認証フロー

1. **Runtime → Gateway**: Cognito M2M認証によるJWT
2. **Gateway → Slack**: Bot Token（API Key）による認証

## 主要機能

### AgentWithIdentityクラス

Cognito M2M認証を使用してAgentCore Identityと統合するエージェントクラスです。

**主要メソッド**:

- `get_access_token()`: AgentCore Identityからアクセストークンを取得
- `access_to_slack()`: 完全な認証フローを実行し、Slackワークスペースにアクセス

### 実装の特徴

- **セキュアな認証**: 2段階認証によるセキュリティ強化
- **ストリーミング対応**: リアルタイムレスポンス処理
- **ページネーション対応**: 大量のツールリストの取得をサポート
- **エラーハンドリング**: 詳細なエラー追跡とログ記録

## 環境変数

| 変数名 | 説明 | 必須 | デフォルト値 |
|--------|------|------|------------|
| `GATEWAY_URL` | Slackツールを提供するGatewayのエンドポイント | ✓ | - |
| `COGNITO_SCOPE` | Cognito OAuth2のスコープ | ✓ | - |
| `WORKLOAD_NAME` | ワークロード名 | - | `slack-gateway-agent` |
| `USER_ID` | ユーザーID | - | `m2m-user-001` |

## 利用可能なSlack操作

エージェントは以下のSlack操作を実行できます

- チャンネル一覧の取得と検索
- メッセージの送信（チャンネルまたはスレッド）
- チャンネル履歴の取得
- ユーザー情報の確認

## デプロイメント

### 前提条件

- AWS Bedrock AgentCore Runtime環境
- AgentCore Gatewayの設定
- AgentCore Identityプロバイダーの設定
- Slack AppとBot Tokenの準備

### 依存関係

```python
strands
bedrock-agentcore
mcp
boto3
```

### 実行方法

```bash
$ agentcore configure --entrypoint slack_gateway_agent.py -er <AgentCore RuntimeサービスロールARN>

$ agentcore launch \
--env GATEWAY_URL=https://*************** \
--env COGNITO_SCOPE=************ 
```

## 技術詳細

### @requires_access_tokenデコレータ

AgentCore Identityとの統合の核となる機能

```python
@requires_access_token(
    provider_name="agentcore-identity-for-gateway",
    scopes=[self.cognito_scope],
    auth_flow="M2M",
    force_authentication=False,
)
```

このデコレータは内部で以下を処理

1. `_get_workload_access_token`を呼び出してworkload access tokenを取得
2. workload access tokenを使用してOAuth tokenを取得
3. access_tokenパラメータとして関数に注入

### MCPクライアント統合

Model Context Protocol (MCP)を使用したツール統合

```python
mcp_client = MCPClient(create_streamable_http_transport)
```

Bearer token認証を含むHTTPトランスポートを作成し、Gatewayとの通信を確立します。

### ストリーミング処理

非同期ストリーミングによるリアルタイムレスポンス

```python
agent_stream = agent.stream_async(user_message)
async for event in agent_stream:
    yield event
```

## セキュリティ考慮事項

- **トークン管理**: アクセストークンは一時的に保持され、必要に応じて更新
- **スコープ制限**: Cognito scopeにより、アクセス権限を制限
- **エラー隠蔽**: セキュリティ関連のエラーは適切に処理され、詳細情報の漏洩を防止

## 参考資料

- [Strands Agents Documentation](https://strandsagents.com)
- [AWS Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock/)
- [AgentCore Identity実装ガイド](https://qiita.com/har1101/items/aae967fa157b01e414a9)
