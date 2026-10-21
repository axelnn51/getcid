import sys
import json
import os
from ocr_api import process_image

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"success": False, "error": "No image path provided"}))
        return
    img_path = sys.argv[1].strip()
    rescue = sys.argv[2].lower() == 'true' if len(sys.argv) > 2 else False
    if not os.path.exists(img_path):
        print(json.dumps({"success": False, "error": f"Image file not found: {img_path}"}))
        return
    try:
        with open(img_path, 'rb') as f:
            data = f.read()
        res = process_image(data, rescue=rescue)
        print(json.dumps(res))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))

if __name__ == "__main__":
    main()
