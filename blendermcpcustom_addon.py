# Code created by Siddharth Ahuja: www.github.com/ahujasid © 2025
# Arranged and modified by harayoki
import bpy
import math
import json
import threading
import socket
import time
import traceback
from bpy.props import StringProperty, IntProperty, BoolProperty, EnumProperty
import io
from contextlib import redirect_stdout, suppress
from typing import List, Dict, Any, Optional, Literal
import traceback

bl_info = {
    "name": "Blender MCP Custom",
    "author": "harayoki",
    "version": (1, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > BlenderMCPCustom",
    "description": "Connect Blender to Claude via MCP",
    "category": "Interface",
}

PI_DIV_180 = math.pi / 180.0
DEFAULT_LAYOUT_SRC_COLLECTION_NAME = "LayoutSrcObjects"
DEFAULT_LAYOUT_DST_COLLECTION_NAME = "LayoutDstObjects"
LAYOUT_PROP_PREFIX: str = "layout_"
LayoutMode = Literal["new", "edit", "del"]

def iter_layout_idprop_keys(id_obj: bpy.types.ID, prefix: str = LAYOUT_PROP_PREFIX) -> List[str]:
    """
    IDブロック（Objectなど）から、prefixで始まる「文字列型」のIDプロパティのキーを列挙して返す。
    """
    # id_obj.keys() は「IDプロパティ」だけが列挙される（ビルトイン属性は含まれない）
    keys: List[str] = [
        k for k in id_obj.keys()
        if k.startswith(prefix) and isinstance(id_obj.get(k), str)
    ]
    keys.sort()
    return keys


def draw_layout_idprops_section(layout: bpy.types.UILayout, id_obj: Optional[bpy.types.ID], *,
                                title: str = "Layout Properties") -> None:
    """
    Nパネル等の draw() で呼び出す補助。該当のIDプロパティがあれば「入力欄の塊」を描画する。
    無ければ何も描かない（= そのセクションは出ない）。
    """
    if id_obj is None:
        return

    keys: List[str] = iter_layout_idprop_keys(id_obj)
    if not keys:
        return

    box: bpy.types.UILayout = layout.box()
    header = box.row(align=True)
    header.label(text=title, icon='ALIGN_LEFT')

    col: bpy.types.UILayout = box.column(align=True)
    col.use_property_split = False        # ラベル列を無効化 → 入力欄を広く
    col.use_property_decorate = False     # 右端の装飾（歯車等）を非表示

    # オブジェクト名表っ時
    col.label(text=f"Object: {id_obj.name}", icon='OBJECT_DATA')

    for k in keys:
        # IDプロパティはブラケット記法で描画
        label_text = k[len(LAYOUT_PROP_PREFIX):]
        if label_text == "tags":
            label_text += " (comma-separated)"
        col.label(text=label_text)  # ラベルをプロパティ名に
        col.prop(id_obj, f'["{k}"]', text="")  # ラベルを消して全幅入力に


class BlenderMCPCustomServer:
    def __init__(self, host='localhost', port=9877):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.server_thread = None

    def start(self):
        if self.running:
            print("Server is already running")
            return

        self.running = True

        try:
            # Create socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)

            # Start server thread
            self.server_thread = threading.Thread(target=self._server_loop)
            self.server_thread.daemon = True
            self.server_thread.start()

            print(f"BlenderMCP CUSTOM server started on {self.host}:{self.port}")
        except Exception as e:
            print(f"Failed to start server: {str(e)}")
            self.stop()

    def stop(self):
        self.running = False

        # Close socket
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None

        # Wait for thread to finish
        if self.server_thread:
            try:
                if self.server_thread.is_alive():
                    self.server_thread.join(timeout=1.0)
            except:
                pass
            self.server_thread = None

        print("BlenderMCP CUSTOM server stopped")

    def _server_loop(self):
        """Main server loop in a separate thread"""
        print("Server thread started")
        self.socket.settimeout(1.0)  # Timeout to allow for stopping

        while self.running:
            try:
                # Accept new connection
                try:
                    client, address = self.socket.accept()
                    print(f"Connected to client: {address}")

                    # Handle client in a separate thread
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client,)
                    )
                    client_thread.daemon = True
                    client_thread.start()
                except socket.timeout:
                    # Just check running condition
                    continue
                except Exception as e:
                    print(f"Error accepting connection: {str(e)}")
                    time.sleep(0.5)
            except Exception as e:
                print(f"Error in server loop: {str(e)}")
                if not self.running:
                    break
                time.sleep(0.5)

        print("Server thread stopped")

    def _handle_client(self, client):
        """Handle connected client"""
        print("Client handler started")
        client.settimeout(None)  # No timeout
        buffer = b''

        try:
            while self.running:
                # Receive data
                try:
                    data = client.recv(8192)
                    if not data:
                        print("Client disconnected")
                        break

                    buffer += data
                    try:
                        # Try to parse command
                        command = json.loads(buffer.decode('utf-8'))
                        buffer = b''

                        # Execute command in Blender's main thread
                        def execute_wrapper():
                            try:
                                response = self.execute_command(command)
                                response_json = json.dumps(response)
                                try:
                                    client.sendall(response_json.encode('utf-8'))
                                except:
                                    print("Failed to send response - client disconnected")
                            except Exception as e:
                                print(f"Error executing command: {str(e)}")
                                traceback.print_exc()
                                try:
                                    error_response = {
                                        "status": "error",
                                        "message": str(e)
                                    }
                                    client.sendall(json.dumps(error_response).encode('utf-8'))
                                except:
                                    pass
                            return None

                        # Schedule execution in main thread
                        bpy.app.timers.register(execute_wrapper, first_interval=0.0)
                    except json.JSONDecodeError:
                        # Incomplete data, wait for more
                        pass
                except Exception as e:
                    print(f"Error receiving data: {str(e)}")
                    break
        except Exception as e:
            print(f"Error in client handler: {str(e)}")
        finally:
            try:
                client.close()
            except:
                pass
            print("Client handler stopped")

    def execute_command(self, command):
        """Execute a command in the main Blender thread"""
        try:
            return self._execute_command_internal(command)

        except Exception as e:
            print(f"Error executing command: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def _execute_command_internal(self, command):
        """Internal command execution with proper context"""
        cmd_type = command.get("type")
        params = command.get("params", {})

        # Base handlers that are always available
        handlers = {
            "ping": self.ping,
            "get_viewport_screenshot": self.get_viewport_screenshot,
            "execute_code": self.execute_code,
            "list_layout_assets": self.list_layout_assets,
            "get_layout_data": self.get_layout_data,
            "locate_objects_batched": self.locate_objects_batched,
        }

        handler = handlers.get(cmd_type)
        if handler:
            try:
                print(f"Executing handler for {cmd_type}")
                result = handler(**params)
                print(f"Handler execution complete")
                return {"status": "success", "result": result}
            except Exception as e:
                print(f"Error in handler: {str(e)}")
                traceback.print_exc()
                return {"status": "error", "message": str(e)}
        else:
            return {"status": "error", "message": f"Unknown command type: {cmd_type}"}

    def ping(self):
        """
        Simple ping command to check connectivity
        """
        return {
            "success": True
        }

    def get_viewport_screenshot(self, max_size=800, filepath=None, format="png"):
        """
        Capture a screenshot of the current 3D viewport and save it to the specified path.

        Parameters:
        - max_size: Maximum size in pixels for the largest dimension of the image
        - filepath: Path where to save the screenshot file
        - format: Image format (png, jpg, etc.)

        Returns success/error status
        """
        try:
            if not filepath:
                return {"error": "No filepath provided"}

            # Find the active 3D viewport
            area = None
            for a in bpy.context.screen.areas:
                if a.type == 'VIEW_3D':
                    area = a
                    break

            if not area:
                return {"error": "No 3D viewport found"}

            # Take screenshot with proper context override
            with bpy.context.temp_override(area=area):
                bpy.ops.screen.screenshot_area(filepath=filepath)

            # Load and resize if needed
            img = bpy.data.images.load(filepath)
            width, height = img.size

            if max(width, height) > max_size:
                scale = max_size / max(width, height)
                new_width = int(width * scale)
                new_height = int(height * scale)
                img.scale(new_width, new_height)

                # Set format and save
                img.file_format = format.upper()
                img.save()
                width, height = new_width, new_height

            # Cleanup Blender image data
            bpy.data.images.remove(img)

            return {
                "success": True,
                "width": width,
                "height": height,
                "filepath": filepath
            }

        except Exception as e:
            return {"error": str(e)}

    def execute_code(self, code):
        """Execute arbitrary Blender Python code"""
        # This is powerful but potentially dangerous - use with caution
        try:
            # Create a local namespace for execution
            namespace = {"bpy": bpy}

            # Capture stdout during execution, and return it as result
            capture_buffer = io.StringIO()
            with redirect_stdout(capture_buffer):
                exec(code, namespace)

            captured_output = capture_buffer.getvalue()
            return {"executed": True, "result": captured_output}
        except Exception as e:
            raise Exception(f"Code execution error: {str(e)}")

    def list_layout_assets(self):
        """
        シーンにコピー配置できるオブジェクトやインスタンスのリストを返す
        リストとして返す情報:
        - name: オブジェクト名
        - type: "obj" or "instance"
        - desc: layout_descriptionプロパティの内容 AIが配置物が何か理解するのに使う
        - tags: layout_tagsプロパティの内容のリスト タグ名で指示を出す場合に使う
        - collider: "sphere" (現状固定)
        - params: layout_で始まるカスタムプロパティの辞書
        """
        objects = []
        # パネルで設定されているコレクション名を得る
        source_collection_name = bpy.context.scene.bmcpc_src_collection
        source_collection = bpy.data.collections.get(source_collection_name)
        if not source_collection:
            return {"error": f"Collection not found"}
        for obj in source_collection.objects:
            item = {
                "name": obj.name,
                "type": "",
                "desc": "",
                "tags": [],
                "collider": {
                    "type": "sphere",  # colliderの形状 現状固定}
                    "radius": 1.0  # in meters
                },
                "params": {}
            }
            if obj.type == 'MESH':
                item["type"] = "obj"  # object
            elif obj.instance_type == 'COLLECTION':
                item["type"] = "instance"  # instance
            else:
                continue
            for k , v in obj.items():
                if k == LAYOUT_PROP_PREFIX + "desc":
                    item["desc"] = v
                if k == LAYOUT_PROP_PREFIX + "tags":
                    item["tags"] = v.split(",") if isinstance(v, str) else []
                elif k.startswith(LAYOUT_PROP_PREFIX):
                    param_key = k[len(LAYOUT_PROP_PREFIX):]
                    item["params"][param_key] = v
            radius = max(obj.dimensions) / 2.0
            radius = radius / bpy.context.scene.unit_settings.scale_length  # メートル単位に変換
            item["collider"]["radius"] = radius
            objects.append(item)
        return {"assets": objects}

    def get_layout_data(self, num_decimal_places:int = 3, target: str="nl") -> Dict[str, List[Dict[str, Any]]]:
        """
        シーン内の特定のコレクション内のオブジェクトの配置情報を取得する
        レイアウトデータの内容はlocate_objects_batchedに準ずる

        num_decimal_places: 位置、回転、スケールの小数点以下の桁数 通信量削減のため指定する
        target: 取得する情報の種類の組み合わせを指定する
            n: name
            l: location
            r: rotation (in degrees)
            s: scale
            d: desc (layout_descカスタムプロパティ)
            t: tags (layout_tagsカスタムプロパティ)
            p: params (layout_で始まるカスタムプロパティ)
            例: "nlrspdt" (全て) "nlr" (name, location, rotation) "n" (nameのみ) など
        戻り値:　辞書
        """
        print("get_layout_data", json.dumps({"num_decimal_places": num_decimal_places, "target": target}))
        target = target.lower()
        layout_data = []
        dest_collection_name = bpy.context.scene.bmcpc_dst_collection
        dest_collection = bpy.data.collections.get(dest_collection_name)
        if not dest_collection:
            return {"layout_data": layout_data}
        for obj in dest_collection.objects:
            item = {
                "n": obj.name,  # name
                "l": [obj.location.x, obj.location.y, obj.location.z],  # location
                "r": [math.degrees(obj.rotation_euler.x),
                      math.degrees(obj.rotation_euler.y),
                      math.degrees(obj.rotation_euler.z)],  # rotation in degrees
                "s": [obj.scale.x, obj.scale.y, obj.scale.z],  # scale
                "d": "",  # desc
                "t": [],  # tags
                "p": {}  # params
            }
            for k, v in enumerate(item["l"]):
                item["l"][k] = round(v, num_decimal_places)
                # 小数点以下がなければ整数にする
                if item["l"][k] == int(item["l"][k]):
                    item["l"][k] = int(item["l"][k])
            for k, v in enumerate(item["r"]):
                item["r"][k] = round(v, num_decimal_places)
                # 小数点以下がなければ整数にする
                if item["r"][k] == int(item["r"][k]):
                    item["r"][k] = int(item["r"][k])
            for k, v in enumerate(item["s"]):
                item["s"][k] = round(v, num_decimal_places)
                # 小数点以下がなければ整数にする
                if item["s"][k] == int(item["s"][k]):
                    item["s"][k] = int(item["s"][k])

            for k, v in obj.items():
                if k.startswith(LAYOUT_PROP_PREFIX):
                    param_key = k[len(LAYOUT_PROP_PREFIX):]
                    # print(f"Found layout prop: {param_key} = {v}")
                    if param_key == "desc":
                        item["d"] = v
                    elif param_key == "tags":
                        item["t"] = v.split(",") if isinstance(v, str) else []
                    else:
                        item["p"][param_key] = v
            if "d" in item and "d" not in target:
                del item["desc"]
            if "t" in item and "t" not in target:
                del item["tags"]

            # targetに含まれないキーは削除
            for key in list(item.keys()):
                if key not in target:
                    print(f"Removing key {key} from item, target={target}")
                    del item[key]
            print(f"#2 Object {obj.name} layout data: {item}")

            layout_data.append(item)
        print(json.dumps(layout_data, indent=2, ensure_ascii=False))
        return {"layout_data": layout_data}

    def locate_objects_batched(self, layout_data: List[Dict[str, Any]]):
        """
        指定された名前のオブジェクトをコピーしてシーンに配置する
        layout_list: 配置するオブジェクトのリスト
        各要素は以下のキーを持つ辞書
        - m: 動作モード "new" / "edit" / "del" 省略時は "new"
        - sn: オブジェクト名 (list_layout_assetsで得られたname) モ―ドが"new"の場合のみ必須
        - n: 編集対象または新規のオブジェクト名 (省略時はnと同じものから自動で命名)
        - l: 位置 (x, y, z)
        - r: 回転 (x, y, z) in degrees
        - s: スケール (x, y, z)
        - p: その他のパラメータ (辞書)
        戻り値:
        以下を持つ辞書
        - num_edited: 編集したオブジェクトの数
        - num_deleted: 削除したオブジェクトの数
        - num_located: 配置に成功したオブジェクトの数
        - num_errors: エラーが発生したオブジェクトの数
        """
        print(json.dumps(layout_data, indent=2, ensure_ascii=False))
        num_error = 0
        num_located = 0
        num_edited = 0
        num_deleted = 0
        dst_collection = bpy.data.collections.get(
            bpy.context.scene.bmcpc_dst_collection) or bpy.context.collection
        for layout in layout_data:
            mode: LayoutMode = layout.get("m", "new")
            if mode not in ("new", "edit", "del"):
                num_error += 1
                continue
            if mode == "del":
                # コレクションから削除
                obj = bpy.data.objects.get(layout.get("n", ""))
                if obj:
                    for col in obj.users_collection:
                        col.objects.unlink(obj)
                    # データも削除
                    bpy.data.objects.remove(obj)
                    num_deleted += 1
                else:
                    num_error += 1
                continue
            location = layout.get("l", None)
            # メートルで指定されるので必要なら変換
            if location:
                location = [l * bpy.context.scene.unit_settings.scale_length for l in location]
            rotation = layout.get("r", None)
            scale = layout.get("s", None)
            params = layout.get("p", {})
            # "new" or "edit"
            new_obj: Optional[bpy.types.Object] = None
            if mode == "mew":
                src_name = layout.get("n")
                if not src_name:
                    num_error += 1
                    continue
                obj = bpy.data.objects.get(src_name)
                if not obj:
                    num_error += 1
                    continue
                new_name = layout.get("nn", src_name)
                if obj.type == 'MESH':
                    new_obj = duplicate_object_shared_mesh(obj, new_name, dst_collection)
                elif obj.instance_type == 'COLLECTION' and obj.instance_collection:
                    new_obj = duplicate_instance_object(obj, new_name, dst_collection)
                else:
                    num_error += 1
                    continue
            elif mode == "edit":
                new_obj = bpy.data.objects.get(layout.get("n", ""))
            if not new_obj:
                num_error += 1
                continue
            if location is not None:
                new_obj.location = location
            if rotation is not None:
                new_obj.rotation_euler = [r * PI_DIV_180 for r in rotation]
            if scale is not None:
                new_obj.scale = scale
            for k, v in params.items():
                prop_name = f"{LAYOUT_PROP_PREFIX}{k}"
                new_obj[prop_name] = v
            if mode == "new":
                num_located += 1
            else:
                num_edited += 1

        return {
            "results": {
                "num_edited": num_edited,
                "num_deleted": num_deleted,
                "num_located": num_located,
                "num_errors": num_error
            }
        }


def duplicate_object_shared_mesh(
        obj: bpy.types.Object, name:str, link_collection: bpy.types.Collection | None = None
) -> bpy.types.Object:
    """
    - メッシュは共有（data.copy()しない）
    - Geometry Nodesモディファイアを含め、モディファイアはコピーされる
    - ノードグループも共有（node_group.copy()しない）
    - マテリアルは共有
    """
    # オブジェクトを複製（モディファイアやカスタムプロパティはそのままコピーされる）
    new_obj = obj.copy()

    # メッシュを共有 → data.copy() しないので obj.data を参照し続ける
    new_obj.data = obj.data
    new_obj.name = name
    new_obj.rotation_mode = 'XYZ'

    # コレクションにリンク
    (link_collection or bpy.context.collection).objects.link(new_obj)

    return new_obj


def duplicate_instance_object(
        obj: bpy.types.Object, name:str, link_collection: bpy.types.Collection | None = None,
        copy_custom_props: bool = True
) -> bpy.types.Object:
    """
    インスタンスオブジェクトを複製
    カスタムプロパティもコピーする
    """
    new_obj = bpy.data.objects.new(name, None)
    new_obj.instance_type = 'COLLECTION'
    new_obj.instance_collection = obj.instance_collection
    (link_collection or bpy.context.collection).objects.link(new_obj)

    if copy_custom_props:
        custom_props = iter_layout_idprop_keys(obj)
        for k in custom_props:
            new_obj[k] = obj[k]

    return new_obj


# Blender UI Panel
class BLENDERMCPCUSTOM_PT_Panel(bpy.types.Panel):
    bl_label = "Blender MCP Custom"
    bl_idname = "BLENDERMCPCUSTOM_PT_Panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BLMCPCustom'  # パネルのタブ名 長いと邪魔なので短くする

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.prop(scene, "bmcpc_port")

        if not scene.bmcpc_server_running:
            layout.operator("blendermcpcustom.start_server", text="Connect to MCP server")
        else:
            layout.operator("blendermcpcustom.stop_server", text="Disconnect from MCP server")
            layout.label(text=f"Running on port {scene.bmcpc_port}")

        layout.separator()

        layout.label(text="Src collection")
        layout.prop(scene, "bmcpc_src_collection")

        layout.label(text="Dest collection")
        layout.prop(scene, "bmcpc_dst_collection")

        layout.operator("blendermcpcustom.add_layout_custom_props",
                        text="Add custom props to selected")

        obj: Optional[bpy.types.Object] = context.active_object
        draw_layout_idprops_section(layout, obj, title="Layout Properties")


# Operator to start the server
class BLENDERMCPCUSTOM_OT_StartServer(bpy.types.Operator):
    bl_idname = "blendermcpcustom.start_server"
    bl_label = "Connect to Claude"
    bl_description = "Start the BlenderMCP server to connect with Claude"

    def execute(self, context):
        scene = context.scene

        create_default_collections()

        # Create a new server instance
        if not hasattr(bpy.types, "bmcpc_server") or not bpy.types.bmcpc_server:
            bpy.types.bmcpc_server = BlenderMCPCustomServer(port=scene.bmcpc_port)

        # Start the server
        bpy.types.bmcpc_server.start()
        scene.bmcpc_server_running = True

        return {'FINISHED'}

# Operator to stop the server
class BLENDERMCPCUSTOM_OT_StopServer(bpy.types.Operator):
    bl_idname = "blendermcpcustom.stop_server"
    bl_label = "Stop the connection to Claude"
    bl_description = "Stop the connection to Claude"

    def execute(self, context):
        scene = context.scene

        # Stop the server if it exists
        if hasattr(bpy.types, "bmcpc_server") and bpy.types.bmcpc_server:
            bpy.types.bmcpc_server.stop()
            del bpy.types.bmcpc_server

        scene.bmcpc_server_running = False

        return {'FINISHED'}

class BLENDERMCPCUSTOM_OT_AddLayoutCustomProps(bpy.types.Operator):
    bl_idname = "blendermcpcustom.add_layout_custom_props"
    bl_label = "Add layout custom props"
    bl_description = "Add layout_desc and layout_tags custom properties to selected objects"

    def execute(self, context):
        for obj in context.selected_objects:
            if "layout_desc" not in obj:
                obj["layout_desc"] = ""
            if "layout_tags" not in obj:
                obj["layout_tags"] = ""
        return {'FINISHED'}


def create_default_collections():
    """
    bmcpc_src_collectionとbmcpc_dst_collectionのデフォルトコレクションを作成
    """
    # デフォルトのコレクションが存在しない場合は作成
    if bpy.context.scene.bmcpc_src_collection == DEFAULT_LAYOUT_SRC_COLLECTION_NAME:
        ensure_collection_in_scene(bpy.context.scene, DEFAULT_LAYOUT_SRC_COLLECTION_NAME)

    # デフォルトのコレクションが存在しない場合は作成
    if bpy.context.scene.bmcpc_dst_collection == DEFAULT_LAYOUT_DST_COLLECTION_NAME:
        ensure_collection_in_scene(bpy.context.scene, DEFAULT_LAYOUT_DST_COLLECTION_NAME)


def ensure_collection_in_scene(scene: bpy.types.Scene, name: str) -> bpy.types.Collection:
    """
    現在の .blend から `name` のコレクションを取得し、無ければ作成。
    さらに `scene.collection`（シーンのルート）に未リンクならリンクする。
    """
    col: Optional[bpy.types.Collection] = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)

    root: bpy.types.Collection = scene.collection
    # 同じ親に重複リンクしないようチェック
    if root.children.get(col.name) is None:
        root.children.link(col)
    return col


# Registration functions
def register():
    bpy.types.Scene.bmcpc_port = IntProperty(
        name="Port",
        description="Port for the BlenderMCP CUSTOM server",
        default=9877,
        min=1024,
        max=65535
    )

    bpy.types.Scene.bmcpc_server_running = bpy.props.BoolProperty(
        name="Server Running",
        default=False
    )

    bpy.types.Scene.bmcpc_src_collection = StringProperty(
        name="Source Collection",
        description="Name of the collection containing copyable objects",
        default=DEFAULT_LAYOUT_SRC_COLLECTION_NAME
    )

    bpy.types.Scene.bmcpc_dst_collection = StringProperty(
        name="Destination Collection",
        description="Name of the collection where objects will be placed",
        default=DEFAULT_LAYOUT_DST_COLLECTION_NAME
    )

    bpy.utils.register_class(BLENDERMCPCUSTOM_PT_Panel)
    bpy.utils.register_class(BLENDERMCPCUSTOM_OT_StartServer)
    bpy.utils.register_class(BLENDERMCPCUSTOM_OT_StopServer)
    bpy.utils.register_class(BLENDERMCPCUSTOM_OT_AddLayoutCustomProps)


def unregister():
    # Stop the server if it's running
    if hasattr(bpy.types, "bmcpc_server") and bpy.types.bmcpc_server:
        bpy.types.bmcpc_server.stop()
        del bpy.types.bmcpc_server

    bpy.utils.unregister_class(BLENDERMCPCUSTOM_PT_Panel)
    bpy.utils.unregister_class(BLENDERMCPCUSTOM_OT_StartServer)
    bpy.utils.unregister_class(BLENDERMCPCUSTOM_OT_StopServer)
    bpy.utils.unregister_class(BLENDERMCPCUSTOM_OT_AddLayoutCustomProps)

    del bpy.types.Scene.bmcpc_port
    del bpy.types.Scene.bmcpc_server_running
    del bpy.types.Scene.bmcpc_src_collection
    del bpy.types.Scene.bmcpc_dst_collection

    print("BlenderMCP CUSTOM addon unregistered")


if __name__ == "__main__":
    register()
