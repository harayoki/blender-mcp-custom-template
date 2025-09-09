# blender_mcp_server.py
from mcp.server.fastmcp import FastMCP, Context, Image
import socket
import json
import asyncio
import logging
import tempfile
from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any, List, Tuple
import os
from pathlib import Path
from datetime import datetime

# Configure logging
log_file = Path(__file__).parent.parent.parent / "logs" / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[
                        # logging.StreamHandler(),  # コンソール出力
                        logging.FileHandler(log_file.as_posix(), encoding='utf-8')  # ファイル出力
                    ])
logger = logging.getLogger("BlenderMCPCustomServer")

# Default configuration
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9877

@dataclass
class BlenderConnection:
    host: str
    port: int
    sock: socket.socket = None  # Changed from 'socket' to 'sock' to avoid naming conflict
    
    def connect(self) -> bool:
        """Connect to the Blender addon socket server"""
        if self.sock:
            return True
            
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            logger.info(f"Connected to Blender at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Blender: {str(e)}")
            self.sock = None
            return False
    
    def disconnect(self):
        """Disconnect from the Blender addon"""
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Blender: {str(e)}")
            finally:
                self.sock = None

    def receive_full_response(self, sock, buffer_size=8192):
        """Receive the complete response, potentially in multiple chunks"""
        chunks = []
        # Use a consistent timeout value that matches the addon's timeout
        sock.settimeout(15.0)  # Match the addon's timeout
        
        try:
            while True:
                try:
                    chunk = sock.recv(buffer_size)
                    if not chunk:
                        # If we get an empty chunk, the connection might be closed
                        if not chunks:  # If we haven't received anything yet, this is an error
                            raise Exception("Connection closed before receiving any data")
                        break
                    
                    chunks.append(chunk)
                    
                    # Check if we've received a complete JSON object
                    try:
                        data = b''.join(chunks)
                        json.loads(data.decode('utf-8'))
                        # If we get here, it parsed successfully
                        logger.info(f"Received complete response ({len(data)} bytes)")
                        return data
                    except json.JSONDecodeError:
                        # Incomplete JSON, continue receiving
                        continue
                except socket.timeout:
                    # If we hit a timeout during receiving, break the loop and try to use what we have
                    logger.warning("Socket timeout during chunked receive")
                    break
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error during receive: {str(e)}")
                    raise  # Re-raise to be handled by the caller
        except socket.timeout:
            logger.warning("Socket timeout during chunked receive")
        except Exception as e:
            logger.error(f"Error during receive: {str(e)}")
            raise
            
        # If we get here, we either timed out or broke out of the loop
        # Try to use what we have
        if chunks:
            data = b''.join(chunks)
            logger.info(f"Returning data after receive completion ({len(data)} bytes)")
            try:
                # Try to parse what we have
                json.loads(data.decode('utf-8'))
                return data
            except json.JSONDecodeError:
                # If we can't parse it, it's incomplete
                raise Exception("Incomplete JSON response received")
        else:
            raise Exception("No data received")

    def send_command(self, command_type: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Send a command to Blender and return the response"""
        if not self.sock and not self.connect():
            raise ConnectionError("Not connected to Blender")
        
        command = {
            "type": command_type,
            "params": params or {}
        }
        
        try:
            # Log the command being sent
            logger.info(f"Sending command: {command_type} with params: {params}")
            
            # Send the command
            self.sock.sendall(json.dumps(command).encode('utf-8'))
            logger.info(f"Command sent, waiting for response...")
            
            # Set a timeout for receiving - use the same timeout as in receive_full_response
            self.sock.settimeout(15.0)  # Match the addon's timeout
            
            # Receive the response using the improved receive_full_response method
            response_data = self.receive_full_response(self.sock)
            logger.info(f"Received {len(response_data)} bytes of data")
            
            response = json.loads(response_data.decode('utf-8'))
            logger.info(f"Response parsed, status: {response.get('status', 'unknown')}")
            
            if response.get("status") == "error":
                logger.error(f"Blender error: {response.get('message')}")
                raise Exception(response.get("message", "Unknown error from Blender"))
            
            return response.get("result", {})
        except socket.timeout:
            logger.error("Socket timeout while waiting for response from Blender")
            # Don't try to reconnect here - let the get_blender_connection handle reconnection
            # Just invalidate the current socket so it will be recreated next time
            self.sock = None
            raise Exception("Timeout waiting for Blender response - try simplifying your request")
        except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
            logger.error(f"Socket connection error: {str(e)}")
            self.sock = None
            raise Exception(f"Connection to Blender lost: {str(e)}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from Blender: {str(e)}")
            # Try to log what was received
            if 'response_data' in locals() and response_data:
                logger.error(f"Raw response (first 200 bytes): {response_data[:200]}")
            raise Exception(f"Invalid response from Blender: {str(e)}")
        except Exception as e:
            logger.error(f"Error communicating with Blender: {str(e)}")
            # Don't try to reconnect here - let the get_blender_connection handle reconnection
            self.sock = None
            raise Exception(f"Communication error with Blender: {str(e)}")

@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """Manage server startup and shutdown lifecycle"""
    # We don't need to create a connection here since we're using the global connection
    # for resources and tools
    
    try:
        # Just log that we're starting up
        logger.info("BlenderMCPCustom server starting up")
        
        # Try to connect to Blender on startup to verify it's available
        try:
            # This will initialize the global connection if needed
            blender = get_blender_connection()
            logger.info("Successfully connected to Blender on startup")
        except Exception as e:
            logger.warning(f"Could not connect to Blender on startup: {str(e)}")
            logger.warning("Make sure the Blender addon is running before using Blender resources or tools")
        
        # Return an empty context - we're using the global connection
        yield {}
    finally:
        # Clean up the global connection on shutdown
        global _blender_connection
        if _blender_connection:
            logger.info("Disconnecting from Blender on shutdown")
            _blender_connection.disconnect()
            _blender_connection = None
        logger.info("BlenderMCPCustom server shut down")

# Create the MCP server with lifespan support
mcp = FastMCP(
    "BlenderMCPCustom",
    lifespan=server_lifespan
)

# Resource endpoints

# Global connection for resources (since resources can't access context)
_blender_connection = None


def get_blender_connection():
    """Get or create a persistent Blender connection"""
    global _blender_connection
    
    # If we have an existing connection, check if it's still valid
    if _blender_connection is not None:
        try:
            # First check if PolyHaven is enabled by sending a ping command
            _blender_connection.send_command("ping")
            return _blender_connection
        except Exception as e:
            # Connection is dead, close it and create a new one
            logger.warning(f"Existing connection is no longer valid: {str(e)}")
            try:
                _blender_connection.disconnect()
            except:
                pass
            _blender_connection = None

    # Create a new connection if needed
    if _blender_connection is None:
        host = os.getenv("BLENDER_HOST", DEFAULT_HOST)
        port = int(os.getenv("BLENDER_PORT", DEFAULT_PORT))
        _blender_connection = BlenderConnection(host=host, port=port)
        if not _blender_connection.connect():
            logger.error("Failed to connect to Blender")
            _blender_connection = None
            raise Exception("Could not connect to Blender. Make sure the Blender addon is running.")
        logger.info("Created new persistent connection to Blender")
    
    return _blender_connection


@mcp.tool()
def get_viewport_screenshot(ctx: Context, max_size: int = 800) -> Image:
    """
    Capture a screenshot of the current Blender 3D viewport.
    
    Parameters:
    - max_size: Maximum size in pixels for the largest dimension (default: 800)
    
    Returns the screenshot as an Image.
    """
    try:
        blender = get_blender_connection()
        
        # Create temp file path
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"blender_screenshot_{os.getpid()}.png")
        
        result = blender.send_command("get_viewport_screenshot", {
            "max_size": max_size,
            "filepath": temp_path,
            "format": "png"
        })
        
        if "error" in result:
            raise Exception(result["error"])
        
        if not os.path.exists(temp_path):
            raise Exception("Screenshot file was not created")
        
        # Read the file
        with open(temp_path, 'rb') as f:
            image_bytes = f.read()
        
        # Delete the temp file
        os.remove(temp_path)
        
        return Image(data=image_bytes, format="png")
        
    except Exception as e:
        logger.error(f"Error capturing screenshot: {str(e)}")
        raise Exception(f"Screenshot failed: {str(e)}")


# @mcp.tool()
# def execute_blender_code(ctx: Context, code: str) -> str:
#     """
#     Execute arbitrary Python code in Blender. Make sure to do it step-by-step by breaking it into smaller chunks.
#
#     Parameters:
#     - code: The Python code to execute
#     """
#     try:
#         # Get the global connection
#         blender = get_blender_connection()
#         result = blender.send_command("execute_code", {"code": code})
#         return f"Code executed successfully: {result.get('result', '')}"
#     except Exception as e:
#         logger.error(f"Error executing code: {str(e)}")
#         return f"Error executing code: {str(e)}"


@mcp.tool()
def list_layout_assets(ctx: Context) -> List[Dict[str, Any]]:
    """
    List available layout assets from the Blender addon.
    Returns a list of asset info object.
    Represents information about objects or instances that can be copied and placed.
    Asset info object is dictionary like:
    {
        "name": "Asset Name",
        "type": "Asset Type",  # "obj" or "instance"
        "desc": "Description of the asset",
        "tags": ["tag1", "tag2"],  # list of tags
        "collider": {
            "type": "sphere",  # currently only "sphere" is supported
            "radius": 1.0  # radius in meters
        },
        # additional properties used for tweaking the asset
        "params": {
            # How to use these properties should be described in the asset description
            "prop1": "value1",
            "prop2": "value2"
        }
    }
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("list_layout_assets", {})
        return result.get("assets", [])
    except Exception as e:
        logger.error(f"Error listing layout assets: {str(e)}")
        raise Exception(f"Could not list layout assets: {str(e)}")

@mcp.tool()
def get_layout_data(ctx: Context, num_decimal_places:int = 3, target: str = "nl") -> Dict[str, Any]:
    """
    Get current layout data from the Blender scene.
    Returns a dictionary with "objects" key, which is a list of object info dictionaries.
    Each object info dictionary is like:
    {
        "n": "Object Name",
        "l": [x, y, z],
        "r": [roll, pitch, yaw],
        "s": [sx, sy, sz],
        "d": "Asset Description",
        "t": ["tag1", "tag2"],  # list of tags
        "p": { ... }  # parameters used to tweak the object, as described in the asset info
        # desc and tags are not included here( for reducing data size)

        # Numbers without decimal places are sent as integers.

    }
    num_decimal_places: Number of decimal places to round location,
        rotation, and scale values (default: 3) used for smaller data size.
    target: A string specifying which properties to include:
        "n": name
        "l": location
        "r": rotation
        "s": scale
        "d": description
        "t": tags
        "p": parameters
        Default is "nl" (name and location).
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "get_layout_data", {"num_decimal_places": num_decimal_places, "target": target})
        return result.get("layout_data", [])
    except Exception as e:
        logger.error(f"Error getting layout data: {str(e)}")
        raise Exception(f"Could not get layout data: {str(e)}")

@mcp.tool()
def locate_objects_batched(ctx: Context, layout_data: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Locate multiple objects in the Blender scene by their names.
    if m(mode) is "layout", the object will be placed. if "edit", the object will be modified. if "del", the object will be deleted.
    if delete mode, only "n" (name) is required.
    Note: Blender’s coordinate system is right-handed: Z is up, Y is depth (backward/forward), X is right. Distances are in meters.
    Parameters:
        ctx: Context
        layout_data: list of layout_data: Each item is a dictionary like:
    {
        "sn":"Src Name"  # (optional) name of the src object to locate. "new" mode requires this.
        "n":"Name"  # new name or edit / delete target name.
        "l": [x,y,z]  # (optional) location to place, default at (0,0,0)
        "r": [roll, pitch, yaw]  # (optional) rotations in degrees, default is (0,0,0)
        "s": [sx, sy, sz]  # (optional) scales, default is [1,1,1]
        "p": { ... }  # (optional) parameters to tweak the object, as described in the asset info
        "m": mode # (optional) "new" or "del" or "edit" # default is "new"
        l and r and s and p are optional, if not provided, default values will be used.
        To make data smaller, you can omit l, r, s, p if you want to use default values
        and values should be specified with fewer decimal places.
    }
    Returns a dictionary, which has "num_located" and "num_errors" keys.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "locate_objects_batched", {"layout_data": layout_data})
        return result.get("results", {})
    except Exception as e:
        logger.error(f"Error locating objects: {str(e)}")
        raise Exception(f"Could not locate objects: {str(e)}")

@mcp.prompt()
def layout_strategy() -> str:
    """Strategy for Planning a Scene Layout in Blender Based on User Requirements
1.Check available assets with the list_layout_assets tool.
This provides information about assets that can be copied and placed, including the asset’s name, type (object or instance), description, tags, collision data, and adjustable parameters.

2.Analyze the user’s requirements.
Identify the types of objects needed, their quantities, placement, rotation, scale, and adjustable parameters.
If the user specifies tags directly, follow those.
If not, select assets with appropriate tags based on their descriptions.
Sometimes there may be no tags; in that case, use the description to choose suitable assets.
The description may include detailed information about the asset’s appearance and intended use.
If information is available on which axes can be rotated or scaled, follow it. If not, make reasonable assumptions.
For adjustable parameters, descriptions may explain their purpose or valid ranges. If not, infer their meaning from the parameter names (e.g., shape variation, color, or depth relative to the ground).

3.Place assets using the locate_objects_batched tool.
This tool allows multiple objects to be placed efficiently at once.
Blender’s coordinate system is right-handed: Z is up, Y is depth (backward/forward), X is right. Distances are in meters.
The same asset may be reused multiple times in the layout (this is encouraged).
Objects include collision information; by default, avoid overlaps, unless the user explicitly allows them.
Unless otherwise specified, placement patterns should avoid a grid-like arrangement and instead appear natural rather than mechanical.
Where appropriate and feasible, adjust size and rotation to achieve a more natural look.”
Ground contact is not considered for now; assume Z=0 is the ground level.
If the user does not specify what the objects should be placed on or their height (Z position),
place objects that would naturally be on the ground at height 0, and position others at an appropriate height.
Object names should be based on the source asset’s name, adjusted to be unique in the scene.
Specify placement, rotation, scale, and any adjustable parameters for each object.

4.Summarize results for the user.
After placement, present the return values from locate_objects_batched in clear, user-friendly language.

# Do not attempt alternative processing with execute_blender_code if any tool execution fails, as this may compromise scene integrity.
"""
    return """Strategy for Planning a Scene Layout in Blender Based on User Requirements

1.Check available assets with the list_layout_assets tool.
This provides information about assets that can be copied and placed, including the asset’s name, type (object or instance), description, tags, collision data, and adjustable parameters.

2.Analyze the user’s requirements.
Identify the types of objects needed, their quantities, placement, rotation, scale, and adjustable parameters.
If the user specifies tags directly, follow those.
If not, select assets with appropriate tags based on their descriptions.
Sometimes there may be no tags; in that case, use the description to choose suitable assets.
The description may include detailed information about the asset’s appearance and intended use.
If information is available on which axes can be rotated or scaled, follow it. If not, make reasonable assumptions.
For adjustable parameters, descriptions may explain their purpose or valid ranges. If not, infer their meaning from the parameter names (e.g., shape variation, color, or depth relative to the ground).

3.Place assets using the locate_objects_batched tool.
This tool allows multiple objects to be placed efficiently at once.
Blender’s coordinate system is right-handed: Z is up, Y is depth (backward/forward), X is right. Distances are in meters.
The same asset may be reused multiple times in the layout (this is encouraged).
Objects include collision information; by default, avoid overlaps, unless the user explicitly allows them.
Unless otherwise specified, placement patterns should avoid a grid-like arrangement and instead appear natural rather than mechanical.
Where appropriate and feasible, adjust size and rotation to achieve a more natural look.”
Ground contact is not considered for now; assume Z=0 is the ground level.
If the user does not specify what the objects should be placed on or their height (Z position), 
place objects that would naturally be on the ground at height 0, and position others at an appropriate height.
Object names should be based on the source asset’s name, adjusted to be unique in the scene.
Specify placement, rotation, scale, and any adjustable parameters for each object.

4.Summarize results for the user.
After placement, present the return values from locate_objects_batched in clear, user-friendly language.

# Do not attempt alternative processing with execute_blender_code if any tool execution fails, as this may compromise scene integrity.
"""

# geminiはメソッドの説明コメントを見ていた
# もとのBlenderMCP実装ではreturnの文字列で説明していた
# どちらが正しいかわからないので両方入れておく

"""ユーザからの要求に基づき、Blender内でシーンレイアウトを計画するための戦略を提供します。

１．まずlist_layout_assetsツールを使用してコピーして配置可能なアセットを確認します。
ここで得られる情報は、アセットの名前、タイプ（オブジェクトまたはインスタンス）、説明、タグ、衝突判定情報、および調整可能なパラメータです。

２．次に、ユーザの要求を分析し、必要なオブジェクトの種類、数量、配置場所、回転、スケール、および調整可能なパラメータを特定します。
ユーザーからはタグの直接指示があればそれに従い、そうでなければアセットの説明をもとに適切なタグがあるものを選びます。
タグは設定がないこともありますが.その場合も説明をもとに適切なアセットを選びます。
説明にはそのオブジェクトの詳細な見た目や使い道の情報も含まれます。
どの軸に回転していいか、スケールを変えていいかの情報があれば従います。指示がなければ推測します。
調整可能なパラメータがある場合、説明にその使い方や設定できる値の範囲が書かれているはずですが、説明がなければパラメータ名から推測します。
見た目の形状の変化や、色の変更、(見た目の）地面への埋まり具合などが想定されています。

３．次にlocate_objects_batchedツールを使用してアセットを配置します。
このツールは一度に複数のオブジェクトを配置できるため、効率的です。
Blenderの座標系は右手系で、Z軸が上方向、Y軸が奥方向、X軸が右方向です。長さはメートル単位で考えます。
レイアウトには必要に応じて同じオブジェクトを複数回使用しても構いません（むしろ推奨されます）。
オブジェクトには衝突判定情報が含まれており、これを考慮して基本はオブジェクト同士が重ならないように配置しますが、
ユーザの指示によっては重なりが許可されます。
配置パターンは言及されない限りグリッド状の配置にならないよう機械的ではなく自然な感じにします。
対象の設定的に自然で可能な範囲で大きさや回転も調整します。
地面との接触は現状考えなくてよいです。Zが0の部分が接地面と考えます。
何の上に配置するか、高さ（Z位置)はどうするかなどの言及がユーザーからない場合、地面にいそうなものは高さ０にそうでないものは適切な高さに配置してください。
配置するオブジェクトの名前はコピー元オブジェクト名をベースに、シーンにユニークであるよう調整します。
配置場所、回転、スケール、および調整可能なパラメータを指定します。

４．最後にlocate_objects_batchedツールの戻り値をユーザーに分かりやすい言葉で伝えます。

※ 各ツール実行で失敗してもexecute_blender_codeを使った代替処理は試みないでください。シーンの整合性が崩れるので。
"""

@mcp.prompt()
def delete_strategy() -> str:
    """Strategy for deleting objects in the scene:
1. Check the current state of the scene.
Use `get_layout_data` to obtain the current object layout.
Follow the method described in get_layout_data_strategy.
If you are certain about the objects to delete, such as those recently placed or edited, you may skip this step to save communication processing. Adjust based on user feedback.
You can also limit the data to be retrieved with the `target` parameter and adjust it as needed. Less data reduces communication load.
2. Identify the objects to delete.
Analyze the user's requirements and determine the names of the objects to delete.
3. Delete the objects.
Use the `locate_objects_batched` tool to delete the target objects from the scene. The required properties are the object name and the "del" specification for `m` (mode).
4. Summarize the results.
Convey the results of the deletion operation to the user in clear and understandable language.
"""
    return """Strategy for deleting objects in the scene:
1. Check the current state of the scene.
Use `get_layout_data` to obtain the current object layout. 
Follow the method described in get_layout_data_strategy.
If you are certain about the objects to delete, such as those recently placed or edited, you may skip this step to save communication processing. Adjust based on user feedback.
You can also limit the data to be retrieved with the `target` parameter and adjust it as needed. Less data reduces communication load.
2. Identify the objects to delete.
Analyze the user's requirements and determine the names of the objects to delete.
3. Delete the objects.
Use the `locate_objects_batched` tool to delete the target objects from the scene. The required properties are the object name and the "del" specification for `m` (mode).
4. Summarize the results.
Convey the results of the deletion operation to the user in clear and understandable language.
"""

"""シーンのオブジェクト削除のための戦略
1.現在のシーンの状況の確認
get_layout_dataで現在のオブジェクトレイアウトを得ます。
やり方はget_layout_data_strategyに準じてください。
もし直前に配置・編集したなど確実に削除したいオブジェクトについてわかっているなら、
通信処理を省くためにこのステップは省略しても構いません。ユーザの反応に合わせてください。
取得するデータもtargetで限定できるので、必要に応じて調整してください。少ないほうが通信量が減ります。
2.削除対象オブジェクトの特定
ユーザの要求を分析し、削除すべきオブジェクト名を特定します。
3.オブジェクトの削除
locate_objects_batchedツールを使用して、削除対象オブジェクトをシーンから削除します。
この際必要となるプロパティはオブジェクト名とm(mode)の"del"指定のみです。
4.結果の要約
削除操作の結果をユーザに分かりやすい言葉で伝えます。
"""

@mcp.prompt()
def edit_strategy() -> str:
    """`Strategy for editing objects in the scene:
1. Check the current state of the scene.
Use `get_layout_data` to obtain the current object layout.
Follow the method described in get_layout_data_strategy.
If you are certain about the objects to edit, such as those recently placed or edited, you may skip this step to save communication processing. Adjust based on user feedback.
2. Identify the objects to edit.
Analyze the user's requirements and determine the names of the objects to edit.
3. Edit the objects.
Use the `locate_objects_batched` tool to edit the target objects in the scene.
Specify "edit" for `m` (mode).
Specify the properties to be changed, such as location, rotation, scale, and adjustable parameters.
4. Summarize the results.
Convey the results of the editing operation to the user in clear and understandable language.
"""
    return """`Strategy for editing objects in the scene:
1. Check the current state of the scene.
Use `get_layout_data` to obtain the current object layout.
Follow the method described in get_layout_data_strategy.
If you are certain about the objects to edit, such as those recently placed or edited, you may skip this step to save communication processing. Adjust based on user feedback.
2. Identify the objects to edit.
Analyze the user's requirements and determine the names of the objects to edit.
3. Edit the objects.
Use the `locate_objects_batched` tool to edit the target objects in the scene.
Specify "edit" for `m` (mode).
Specify the properties to be changed, such as location, rotation, scale, and adjustable parameters.
4. Summarize the results.
Convey the results of the editing operation to the user in clear and understandable language.
"""

"""シーンのオブジェクト編集のための戦略
1.現在のシーンの状況の確認
get_layout_dataで現在のオブジェクトレイアウトを得ます。
やり方はget_layout_data_strategyに準じてください。
もし直前に配置・編集したなど確実に削除したいオブジェクトについてわかっているなら、
通信処理を省くためにこのステップは省略しても構いません。ユーザの反応に合わせてください。
2.編集対象オブジェクトの特定
ユーザの要求を分析し、編集すべきオブジェクト名を特定します。
3.オブジェクトの編集
locate_objects_batchedツールを使用して、編集対象オブジェクトをシーン内で編集します。
m(mode)は"edit"を指定します。
指定するプロパティは、位置、回転、スケール、および調整可能なパラメータのうち、変化があるものです。
4.結果の要約
編集操作の結果をユーザに分かりやすい言葉で伝えます。
"""

@mcp.prompt()
def layout_and_edit_and_delete_strategy() -> str:
    """Strategy for placing, editing, and deleting objects in the scene
The previously mentioned layout_strategy, edit_strategy, and delete_strategy can be executed in any combination simultaneously.
If you want to ensure the process proceeds step by step or confirm with the user at each stage, you can execute them separately.
However, it is recommended to execute them all at once whenever possible.
"""
    return """Strategy for placing, editing, and deleting objects in the scene
The previously mentioned layout_strategy, edit_strategy, and delete_strategy can be executed in any combination simultaneously.
If you want to ensure the process proceeds step by step or confirm with the user at each stage, you can execute them separately.
However, it is recommended to execute them all at once whenever possible.
"""

"""シーンのオブジェクト配置・編集・削除のための戦略
これまで述べたlayout_strategy、edit_strategy、delete_strategyは同時にすきな組み合わせで実行できます。
確実に処理を進めたい場合や、逐次ユーザに確認を入れたい場合はばらばらに実行しても構いませんが、
可能な限り一度にまとめて実行することを推奨します。
"""

@mcp.prompt()
def get_layout_data_strategy() -> str:
    """Strategy for retrieving the current layout data from the scene
    Use the get_layout_data tool to obtain the current layout data from the Blender scene.
    To reduce communication data size, adjust the parameters as needed.
    - num_decimal_places: Specify the number of decimal places for rounding location, rotation, and scale values. This helps reduce data size. The default is 3.
    If decimal places are unnecessary for the overall scale or rough positions are sufficient, set it to 0 as needed.
    - target: Specify the properties to include in the response. Options are as follows:
        - "n": Name
        - "l": Location
        - "r": Rotation
        - "s": Scale
        - "p": Parameters
        - "d": Description
        - "t": Tags
    By default, all properties are included, but reduce them as needed to decrease data size.
    For example, if you only need to confirm existence, specifying "n" or "nl" is sufficient.
    "d" and "t" are usually unnecessary. Add them only when needed, such as for editing or deleting objects with specific characteristics.
    """
    return """Strategy for retrieving the current layout data from the scene
Use the get_layout_data tool to obtain the current layout data from the Blender scene.
To reduce communication data size, adjust the parameters as needed.
- num_decimal_places: Specify the number of decimal places for rounding location, rotation, and scale values. This helps reduce data size. The default is 3.
If decimal places are unnecessary for the overall scale or rough positions are sufficient, set it to 0 as needed.
- target: Specify the properties to include in the response. Options are as follows:
    - "n": Name
    - "l": Location
    - "r": Rotation
    - "s": Scale
    - "p": Parameters
    - "d": Description
    - "t": Tags
By default, all properties are included, but reduce them as needed to decrease data size.
For example, if you only need to confirm existence, specifying "n" or "nl" is sufficient.
"d" and "t" are usually unnecessary. Add them only when needed, such as for editing or deleting objects with specific characteristics.
"""

"""シーンの状態を得るための戦略
get_layout_dataツールを使用して、Blenderシーンから現在のレイアウトデータを取得します。
この際通信データ量を抑えるために、必要に応じてパラメータを調整します。
- num_decimal_places: 位置、回転、スケールの値を丸める小数点以下の桁数を指定します。データサイズを減らすのに役立ちます。デフォルトは3です。
全体のスケール的に小数点以下が必要ない場合や、ざっくり位置を知れればいい際に0にするなどで対応してください。
- target: レスポンスに含めるプロパティを指定します。オプションは以下の通りです。
    - "n": 名前
    - "l": 位置
    - "r": 回転
    - "s": スケール
    - "p": パラメータ
    - "d": 説明
    - "t": タグ
デフォルトでは全部含まれますが、必要に応じて減らしてください。データ量が減ります。
例えば存在確認だけであれば"n"指定もしくは"nl"だけで良いです。
dやtも通常必要ないです。特定の特徴のあるオブジェクトを編集・削除する場合などに必要に応じて追加してください。
"""

def main():
    """Run the MCP server"""
    mcp.run()

if __name__ == "__main__":
    main()