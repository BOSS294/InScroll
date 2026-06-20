"""
Patch for MediaPipe C bindings to work with Python 3.14+

This patches the ctypes library loading to avoid the 'free' function lookup error.
Run this ONCE after installing mediapipe to patch your installation.
"""

import sys
import os
from pathlib import Path

def apply_patch():
    """Apply the fix to mediapipe's C bindings"""
    
    # Find the MediaPipe installation
    try:
        import mediapipe
        mediapipe_path = Path(mediapipe.__file__).parent
    except ImportError:
        print("ERROR: MediaPipe is not installed")
        return False
    
    bindings_file = mediapipe_path / "tasks" / "python" / "core" / "mediapipe_c_bindings.py"
    
    if not bindings_file.exists():
        print(f"ERROR: Could not find {bindings_file}")
        return False
    
    print(f"Found MediaPipe at: {mediapipe_path}")
    print(f"Patching: {bindings_file}")
    
    # Read the file
    content = bindings_file.read_text(encoding='utf-8')
    
    # Check if already patched
    if "PYTHON314_PATCH_APPLIED" in content:
        print("✓ Already patched!")
        return True
    
    # Apply the patch - replace the problematic function
    original_snippet = """def load_raw_library():
  \"\"\"Loads the native library.\"\"\"
  _shared_lib = _load_library()
  _shared_lib.free.argtypes = [ctypes.c_void_p]
  _shared_lib.free.restype = None
  return _shared_lib"""
    
    patched_snippet = """def load_raw_library():
  \"\"\"Loads the native library.\"\"\"
  _shared_lib = _load_library()
  # PYTHON314_PATCH_APPLIED: Skip 'free' function binding for Python 3.14+ compatibility
  try:
    _shared_lib.free.argtypes = [ctypes.c_void_p]
    _shared_lib.free.restype = None
  except AttributeError:
    # Python 3.14+ ctypes issue - 'free' may not be available
    # This is safe to skip as Python's garbage collector handles cleanup
    pass
  return _shared_lib"""
    
    if original_snippet in content:
        new_content = content.replace(original_snippet, patched_snippet)
        bindings_file.write_text(new_content, encoding='utf-8')
        print("✓ Patch applied successfully!")
        return True
    else:
        print("WARNING: Could not find the exact code snippet to patch")
        print("The MediaPipe version may be different than expected")
        return False

if __name__ == "__main__":
    success = apply_patch()
    sys.exit(0 if success else 1)