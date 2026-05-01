# 開発情報

## コマンド

パッケージマネージャーには `uv` を使用しています。

```bash
# 依存関係のインストール
uv sync

# MCP サーバーの起動
uv run blender-mcp
# または
python main.py

# 編集可能モードでインストール
pip install -e .
```

テストスイートはありません。動作確認は Blender を起動した状態でサーバーを実行して手動で行います。

## アーキテクチャ

プロジェクトは TCP ソケット（デフォルト: `localhost:9877`）で通信する 2 つのコンポーネントで構成されています。

### 1. Blender アドオン (`blendermcpcustom_addon.py`)
**Blender 内部**で動作します。TCP ソケットサーバーを起動し、MCP サーバーからのコマンドを受け付けます。Blender の API を安全に呼び出すため、コマンドは `bpy.app.timers.register()` を通じてメインスレッドに委譲されます。

対応コマンド: `get_viewport_screenshot`、`execute_code`、`ping`

UI パネルの場所: View3D > サイドバー > BLMCPCustom タブ

### 2. MCP サーバー (`src/blender_mcp/server.py`)
**外部プロセス**として独立して動作します。`FastMCP` フレームワークを使用し、Claude に 2 つの MCP ツールを公開します。
- `get_viewport_screenshot(max_size)` — 3D ビューポートをキャプチャし PNG 画像を返す
- `execute_blender_code(code)` — Blender のコンテキストで任意の Python コードを実行する

再接続ロジックを持つグローバルな `BlenderConnection` を永続的に維持します。生 TCP 上の JSON プロトコルで、15 秒タイムアウトと分割受信に対応しています。

### 通信フロー
```
Claude → MCP サーバー (FastMCP) → TCP ソケット → Blender アドオン → bpy (Blender Python API)
```

### 設定

| 環境変数        | デフォルト  | 説明                    |
|----------------|-------------|------------------------|
| `BLENDER_HOST` | `localhost` | Blender アドオンのホスト |
| `BLENDER_PORT` | `9877`      | Blender アドオンのポート |

ポートは Blender の UI パネル（シーンプロパティ `blendermcpcustom_port`）からも変更できます。

## 新しいコマンドの追加

新しいコマンド（例: `my_command`）を追加する手順:
1. `blendermcpcustom_addon.py` の `BlenderMCPCustomServer` にハンドラーメソッドを追加する
2. `_execute_command_internal()` 内の `handlers` ディクショナリに登録する
3. `src/blender_mcp/server.py` に `blender.send_command("my_command", {...})` を呼び出す `@mcp.tool()` 関数を追加する

## 開発環境のセットアップ

1. Blender にアドオンをインストールする: `blendermcpcustom_addon.py` を Blender のアドオンディレクトリにコピーするか、環境設定 > アドオン > ファイルからインストール で追加する
2. アドオンを有効化し、BLMCPCustom パネルからソケットサーバーを起動する
3. `uv run blender-mcp` で MCP サーバーを起動する
4. MCP クライアント（Claude Desktop など）をサーバーに接続するよう設定する
