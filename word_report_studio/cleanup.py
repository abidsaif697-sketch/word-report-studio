import os
import shutil

def run_cleanup():
    # Base directory is the word_report_studio folder
    studio_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(studio_dir)
    
    print("--- Word Report Studio: Auto Cache Cleanup ---")
    
    # 1. Output directories to remove
    dirs_to_remove = [
        os.path.join(studio_dir, "output"),
        os.path.join(studio_dir, "output_filled"),
    ]
    
    # Also find any other output_* directories in word_report_studio
    if os.path.exists(studio_dir):
        for item in os.listdir(studio_dir):
            item_path = os.path.join(studio_dir, item)
            if os.path.isdir(item_path) and item.startswith("output_") and item != "output_filled":
                dirs_to_remove.append(item_path)
                
    for d in dirs_to_remove:
        if os.path.exists(d):
            try:
                shutil.rmtree(d)
                print(f"Removed temporary directory: {d}")
            except Exception as e:
                print(f"Notice: Could not remove {d}: {e}")

    # 2. Walk through and clean __pycache__, temporary files, thumbnails, crops, etc.
    for root, dirs, files in os.walk(project_root):
        # Skip git directories
        if ".git" in root.split(os.sep):
            continue
            
        # Clean __pycache__, thumbnails, _photo_crops, _extracted_images_*
        for d in list(dirs):
            if d == "__pycache__" or d == "thumbnails" or d == "_photo_crops" or d.startswith("_extracted_images_"):
                dir_path = os.path.join(root, d)
                try:
                    shutil.rmtree(dir_path)
                    print(f"Removed cache directory: {dir_path}")
                    dirs.remove(d) # Avoid traversing deleted directory
                except Exception as e:
                    pass
                    
        # Clean *.pyc, *.pyo, Thumbs.db, Desktop.ini
        for f in files:
            if f.endswith(".pyc") or f.endswith(".pyo") or f.lower() == "thumbs.db" or f.lower() == "desktop.ini":
                file_path = os.path.join(root, f)
                try:
                    os.remove(file_path)
                    print(f"Removed cache file: {file_path}")
                except Exception as e:
                    pass
                    
    print("----------------------------------------------")

if __name__ == "__main__":
    run_cleanup()
