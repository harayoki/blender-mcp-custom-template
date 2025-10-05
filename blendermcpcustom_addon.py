# Code created by Siddharth Ahuja: www.github.com/ahujasid © 2025
# Arranged and modified by harayoki

import bpy
import json
import threading
import socket
import time
import traceback
from bpy.props import StringProperty, IntProperty, BoolProperty, EnumProperty
import io
from contextlib import redirect_stdout, suppress

bl_info = {
    "name": "Blender MCP Custom",
    "author": "harayoki",
    "version": (1, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > BlenderMCPCustom",
    "description": "Connect Blender to Claude via MCP",
    "category": "Interface",
}


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
            "get_viewport_screenshot": self.get_viewport_screenshot,
            # "execute_code": self.execute_code,
            "ping": self.ping,
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

    # def execute_code(self, code):
    #     """Execute arbitrary Blender Python code"""
    #     # This is powerful but potentially dangerous - use with caution
    #     try:
    #         # Create a local namespace for execution
    #         namespace = {"bpy": bpy}
    #
    #         # Capture stdout during execution, and return it as result
    #         capture_buffer = io.StringIO()
    #         with redirect_stdout(capture_buffer):
    #             exec(code, namespace)
    #
    #         captured_output = capture_buffer.getvalue()
    #         return {"executed": True, "result": captured_output}
    #     except Exception as e:
    #         raise Exception(f"Code execution error: {str(e)}")

    def ping(self):
        """
        Simple ping command to check connectivity
        """
        return {
            "success": True
        }


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

        layout.prop(scene, "blendermcpcustom_port")

        if not scene.blendermcpcustom_server_running:
            layout.operator("blendermcpcustom.start_server", text="Connect to MCP server")
        else:
            layout.operator("blendermcpcustom.stop_server", text="Disconnect from MCP server")
            layout.label(text=f"Running on port {scene.blendermcpcustom_port}")


# Operator to start the server
class BLENDERMCPCUSTOM_OT_StartServer(bpy.types.Operator):
    bl_idname = "blendermcpcustom.start_server"
    bl_label = "Connect to Claude"
    bl_description = "Start the BlenderMCP server to connect with Claude"

    def execute(self, context):
        scene = context.scene

        # Create a new server instance
        if not hasattr(bpy.types, "blendermcpcustom_server") or not bpy.types.blendermcpcustom_server:
            bpy.types.blendermcpcustom_server = BlenderMCPCustomServer(port=scene.blendermcpcustom_port)

        # Start the server
        bpy.types.blendermcpcustom_server.start()
        scene.blendermcpcustom_server_running = True

        return {'FINISHED'}

# Operator to stop the server
class BLENDERMCPCUSTOM_OT_StopServer(bpy.types.Operator):
    bl_idname = "blendermcpcustom.stop_server"
    bl_label = "Stop the connection to Claude"
    bl_description = "Stop the connection to Claude"

    def execute(self, context):
        scene = context.scene

        # Stop the server if it exists
        if hasattr(bpy.types, "blendermcpcustom_server") and bpy.types.blendermcpcustom_server:
            bpy.types.blendermcpcustom_server.stop()
            del bpy.types.blendermcpcustom_server

        scene.blendermcpcustom_server_running = False

        return {'FINISHED'}

# Registration functions
def register():
    bpy.types.Scene.blendermcpcustom_port = IntProperty(
        name="Port",
        description="Port for the BlenderMCP CUSTOM server",
        default=9877,
        min=1024,
        max=65535
    )

    bpy.types.Scene.blendermcpcustom_server_running = bpy.props.BoolProperty(
        name="Server Running",
        default=False
    )

    bpy.utils.register_class(BLENDERMCPCUSTOM_PT_Panel)
    bpy.utils.register_class(BLENDERMCPCUSTOM_OT_StartServer)
    bpy.utils.register_class(BLENDERMCPCUSTOM_OT_StopServer)

    print("BlenderMCP CUSTOM addon registered")

def unregister():
    # Stop the server if it's running
    if hasattr(bpy.types, "blendermcpcustom_server") and bpy.types.blendermcpcustom_server:
        bpy.types.blendermcpcustom_server.stop()
        del bpy.types.blendermcpcustom_server

    bpy.utils.unregister_class(BLENDERMCPCUSTOM_PT_Panel)
    bpy.utils.unregister_class(BLENDERMCPCUSTOM_OT_StartServer)
    bpy.utils.unregister_class(BLENDERMCPCUSTOM_OT_StopServer)

    del bpy.types.Scene.blendermcpcustom_port
    del bpy.types.Scene.blendermcpcustom_server_running

    print("BlenderMCP CUSTOM addon unregistered")


if __name__ == "__main__":
    register()
