import os
import shutil
import glob

def cleanup():
    # Base directory is the workspace root
    base_dir = os.path.dirname(os.path.abspath(__file__))
    word_report_studio_dir = os.path.join(base_dir, "word_report_studio")
    
    print(f"Starting cleanup in: {base_dir}")
    
    # 1. Directories to remove completely if they exist
    dirs_to_remove = [
        os.path.join(word_report_studio_dir, "output"),
        os.path.join(word_report_studio_dir, "output_filled"),
    ]
    
    # Also find any other output_* directories in word_report_studio
    if os.path.exists(word_report_studio_dir):
        for item in os.listdir(word_report_studio_dir):
            item_path = os.path.join(word_report_studio_dir, item)
            if os.path.isdir(item_path) and item.startswith("output_") and item != "output_filled":
                dirs_to_remove.append(item_path)
                
    for d in dirs_to_remove:
        if os.path.exists(d):
            try:
                shutil.rmtree(d)
                print(f"Successfully removed directory: {d}")
            except Exception as e:
                print(f"Error removing directory {d}: {e}")

    # 2. Walk through and clean pycache, temporary files, thumbnails, crops, etc.
    for root, dirs, files in os.walk(base_dir):
        # Skip git files
        if ".git" in root.split(os.sep):
            continue
            
        # Clean __pycache__, thumbnails, _photo_crops, _extracted_images_*
        for d in list(dirs):
            if d == "__pycache__" or d == "thumbnails" or d == "_photo_crops" or d.startswith("_extracted_images_"):
                dir_path = os.path.join(root, d)
                try:
                    shutil.rmtree(dir_path)
                    print(f"Successfully removed directory: {dir_path}")
                    dirs.remove(d) # Don't traverse into deleted directory
                except Exception as e:
                    print(f"Error removing directory {dir_path}: {e}")
                    
        # Clean *.pyc, *.pyo, Thumbs.db, Desktop.ini
        for f in files:
            if f.endswith(".pyc") or f.endswith(".pyo") or f.lower() == "thumbs.db" or f.lower() == "desktop.ini":
                file_path = os.path.join(root, f)
                try:
                    os.remove(file_path)
                    print(f"Successfully removed file: {file_path}")
                except Exception as e:
                    print(f"Error removing file {file_path}: {e}")

    print("Cleanup complete!")

if __name__ == "__main__":
    cleanup()
