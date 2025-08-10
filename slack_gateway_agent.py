from strands import Agent
from strands.tools.mcp import MCPClient
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from typing import Any, Dict, List, Optional
import logging
from boto3.session import Session
import os

# MCPクライアント用のインポート
from mcp.client.streamable_http import streamablehttp_client

# AgentCore Identityからアクセストークンを取得する
from bedrock_agentcore.identity.auth import requires_access_token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

boto_session = Session()
region = boto_session.region_name

class AgentWithIdentity:
    """
    Cognito M2M認証を使用したAgentCore Identityを利用するエージェント。
    
    必要な環境変数：
    - GATEWAY_URL: Slackツールを提供するGatewayのエンドポイント
    - COGNITO_SCOPE: Cognito OAuth2のスコープ
    - WORKLOAD_NAME: （オプション）workload名、デフォルトは"slack-gateway-agent"
    - USER_ID: (オプション)user-idを設定する、デフォルトは"m2m-user-001"
    """

    def __init__(self):
        self.gateway_url = os.environ.get("GATEWAY_URL")
        self.cognito_scope = os.environ.get("COGNITO_SCOPE")
        self.workload_name = os.environ.get("WORKLOAD_NAME", "slack-gateway-agent")
        self.user_id = os.environ.get("USER_ID", "m2m-user-001")
        self.region = region
        
        # 環境変数の検証
        if not self.gateway_url:
            raise ValueError("GATEWAY_URL環境変数が必要です")
        if not self.cognito_scope:
            raise ValueError("COGNITO_SCOPE環境変数が必要です")
            
        logger.info(f"Gateway URL: {self.gateway_url}")
        logger.info(f"Cognito scope: {self.cognito_scope}")
        logger.info(f"Workload name: {self.workload_name}")
        logger.info(f"User ID: {self.user_id}")
        logger.info(f"AWS Region: {self.region}")

    async def get_access_token(self) -> str:
        """AgentCore Identityを使用してアクセストークンを取得する。
        
        Runtime環境では、runtimeUserIdはInvokeAgentRuntime API呼び出し時に
        システム側が設定し、Runtimeがエージェントに渡します。
        
        Returns:
            str: 認証されたAPIコール用のアクセストークン
        """
        
        # @requires_access_tokenデコレータ付きのラッパー関数を作成
        # Runtime環境では、デコレータが内部で_get_workload_access_tokenを呼び出し、
        # workload access tokenを自動的に取得する
        @requires_access_token(
            provider_name="agentcore-identity-for-gateway",
            scopes=[self.cognito_scope],
            auth_flow="M2M",
            force_authentication=False,
        )
        async def _get_token(*, access_token: str) -> str:
            """
            AgentCore Identityからアクセストークンを受け取る内部関数。
            
            デコレータが内部で以下を処理：
            1. _get_workload_access_tokenを呼び出してworkload access tokenを取得
                - workload_name: Runtime環境から取得
                - user_id: InvokeAgentRuntimeのruntimeUserIdヘッダーから取得
            2. workload access tokenを使用してOAuth tokenを取得
            3. access_tokenパラメータとして注入
            
            Args:
                access_token: OAuthアクセストークン（デコレータによって注入）
                
            Returns:
                str: APIコールで使用するアクセストークン
            """
            logger.info("✅ AgentCore Identity経由でアクセストークンの取得に成功")
            logger.info(f"   Workload name: {self.workload_name}")
            logger.info(f"   トークンプレフィックス: {access_token[:20]}...")
            logger.info(f"   トークン長: {len(access_token)} 文字")
            return access_token
        
        # デコレータ付き関数を呼び出してトークンを取得
        return await _get_token()
    
    async def access_to_slack(self, payload: Dict[str, Any]):
        """
        完全なフロー: トークン取得 → エージェント作成 → ストリーミングでSlackワークスペースにアクセス。
        
        これはAgentCore Identityの推奨される2ステップパターンを示しています：
        1. @requires_access_tokenを使用してアクセストークンを取得
        2. トークンを使用して認証されたクライアントを作成し、操作を実行

        Args:
            payload: ユーザープロンプトを含むAgentCore Runtimeペイロード

        Yields:
            エージェントからのストリーミングレスポンスイベント
        """

        # ステップ1: AgentCore Identityを使用してアクセストークンを取得
        logger.info("ステップ1: AgentCore Identity経由でアクセストークンを取得中...")
        logger.info(f"Runtimeが自動的にruntimeUserIdを渡します")
        
        access_token = await self.get_access_token()
        
        # ステップ2: 認証されたMCPクライアントでエージェントを作成
        logger.info("ステップ2: 認証されたMCPクライアントでエージェントを作成中...")

        def create_streamable_http_transport():
            """
            Bearerトークン認証を使用したストリーミング可能なHTTPトランスポートを作成。
            
            このトランスポートは、MCPクライアントがGatewayへの認証された
            リクエストを行うために使用されます。
            """
            logger.info(f"🔗 MCP transport作成中: {self.gateway_url}")
            logger.info(f"🔑 トークンプレフィックス: {access_token[:20]}...")
            transport = streamablehttp_client(
                self.gateway_url, 
                headers={"Authorization": f"Bearer {access_token}"}
            )
            logger.info("✅ MCP transport作成完了")
            return transport
        
        def get_full_tools_list(client):
            """
            ページネーションをサポートしてすべての利用可能なツールをリスト。
            
            Gatewayはページネーションされたレスポンスでツールを返す可能性があるため、
            完全なリストを取得するためにページネーションを処理する必要があります。
            
            Args:
                client: MCPクライアントインスタンス
                
            Returns:
                list: 利用可能なツールの完全なリスト
            """
            more_tools = True
            tools = []
            pagination_token = None
            
            while more_tools:
                tmp_tools = client.list_tools_sync(pagination_token=pagination_token)
                tools.extend(tmp_tools)
                
                if tmp_tools.pagination_token is None:
                    more_tools = False
                else:
                    more_tools = True 
                    pagination_token = tmp_tools.pagination_token
            return tools
        
        # 認証されたトランスポートでMCPクライアントを作成
        mcp_client = MCPClient(create_streamable_http_transport)

        try:
            with mcp_client:
                # ステップ3: 認証された接続を通じて利用可能なツールをリスト
                logger.info("ステップ3: 認証されたMCPクライアント経由で利用可能なツールをリスト中...")
                tools = get_full_tools_list(mcp_client)
                # MCPツールの属性名を確認してからログ出力
                try:
                    tools_names = [getattr(tool, 'tool_name', getattr(tool, 'name', str(tool))) for tool in tools]
                except Exception as e:
                    logger.warning(f"ツール名の取得に失敗: {e}")
                    tools_names = [str(tool) for tool in tools]
                logger.info(f"利用可能なツール: {tools_names}")

                if not tools:
                    raise RuntimeError("Gatewayから利用可能なツールがありません")
                
                # ステップ4: 認証されたツールでエージェントを作成
                logger.info("ステップ4: 認証されたツールでStrands Agentを作成中...")
                agent = Agent(
                    tools=tools,
                    model="us.anthropic.claude-sonnet-4-20250514-v1:0",
                    system_prompt=
                    """
                    あなたはSlack統合アシスタントです。

                    以下の操作が可能です：
                    - チャンネル一覧の取得と検索
                    - メッセージの送信（チャンネルまたはスレッド）
                    - チャンネル履歴の取得
                    - ユーザー情報の確認

                    ユーザーのリクエストを理解し、適切なSlack操作を実行してください。
                    操作結果は明確に報告してください。
                    """
                )

                # ステップ5: ストリーミングでSlackワークスペースにアクセス
                logger.info("ステップ5: ストリーミングでSlackワークスペースにアクセス中...")
                # ユーザーメッセージを取得
                user_message = payload.get("prompt", "")
                logger.info(f"ユーザーメッセージ: {user_message}")
                
                # ストリーミングレスポンスを使用
                agent_stream = agent.stream_async(user_message)
                
                # ストリーミングイベントをyieldで返す
                async for event in agent_stream:
                    # デバッグ用：ツール実行に関するイベントをログ出力
                    if isinstance(event, dict):
                        if event.get('current_tool_use'):
                            tool_info = event.get('current_tool_use')
                            logger.info(f"🔧 ツール実行中: {tool_info}")
                        elif event.get('delta') and event['delta'].get('toolUse'):
                            logger.info(f"🚀 ツール呼び出し開始: {event['delta']['toolUse']}")
                        elif 'data' in event and 'Tool #' in str(event.get('data', '')):
                            logger.info(f"📋 ツール情報: {event['data']}")
                    yield event
                    
                logger.info(f"Slackへのアクセス完了")
                
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            logger.error(f"❌ エージェント実行中のエラー: {e}")
            logger.error(f"📊 エラーの詳細トレース:\n{error_trace}")
            
            # エラーメッセージの詳細分析
            error_msg = str(e).lower()
            if "read timeout" in error_msg:
                if "none" in error_msg:
                    logger.error("🔍 URLがNoneになっている問題を検出")
                    yield {"error": f"Gateway URL設定エラー: {str(e)}"}
                else:
                    logger.error("⏱️ 読み取りタイムアウトを検出")
                    yield {"error": f"Gateway応答タイムアウト: {str(e)}"}
            else:
                yield {"error": f"エージェントの実行に失敗しました: {str(e)}"}

# AgentCoreアプリケーションを初期化
app = BedrockAgentCoreApp()

@app.entrypoint
async def slack_agent(payload: Dict[str, Any]):
    """Slackツール連携エージェントのメインエントリーポイント
    
    Args:
        payload: AgentCore Runtimeから渡されるペイロード
                - prompt: ユーザーからの入力メッセージ
    
    Yields:
        AgentCore Runtime形式のストリーミングレスポンス
    """
    
    try:
        # AgentWithIdentityインスタンスを作成
        agent_with_identity = AgentWithIdentity()
    except ValueError as e:
        # 環境変数が設定されていない場合のエラー
        logger.error(f"設定エラー: {e}")
        yield {"error": f"設定エラー: {str(e)}. GATEWAY_URLとCOGNITO_SCOPE環境変数が設定されていることを確認してください。"}
        return
    except Exception as e:
        # その他の初期化エラー
        logger.error(f"初期化エラー: {e}")
        yield {"error": f"エージェントの初期化に失敗しました: {str(e)}"}
        return
    
    # プロンプトの検証
    if not payload or "prompt" not in payload:
        yield {"error": "無効なペイロード: 'prompt'フィールドが必要です"}
        return
    
    try:
        # ストリーミングレスポンスを転送
        async for event in agent_with_identity.access_to_slack(payload):
            # エラーイベントの場合はそのまま返す
            if "error" in event:
                yield event
            # データイベントの場合は適切な形式で返す
            elif "data" in event:
                yield event
            # その他のイベント（ツール使用など）もそのまま返す
            else:
                yield event
                
    except Exception as e:
        logger.error(f"slack_agentでエラー: {e}", exc_info=True)
        yield {"error": f"リクエストの処理中にエラーが発生しました: {str(e)}"}

if __name__ == "__main__":
    # Slackツール連携エージェントサーバーを起動
    # デフォルトでポート8080でリッスンします
    logger.info("Slackツール連携エージェントサーバーを起動中...")
    app.run()