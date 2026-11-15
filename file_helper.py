import os
from datetime import datetime


class FileHelper:

    def __init__(self, path):
        self.path = path

    def exists(self):
        return os.path.isdir(self.path)

    def get_file_with_date(self, date_from, date_to, extensions):

        date_from = datetime.strptime(
            date_from, "%Y-%m-%d"
        ).date()

        date_to = datetime.strptime(
            date_to, "%Y-%m-%d"
        ).date()

        

        all_files = "*" in extensions

        extensions = {
            ext.lower() if ext.startswith(".") else "." + ext.lower()
            for ext in extensions
            if ext != "*"
        }

        files = []
        
        

        def scan(folder):

            try:
                with os.scandir(folder) as entries:

                    for entry in entries:

                        try:

                            if entry.is_dir(follow_symlinks=False):
                                scan(entry.path)
                                continue

                            if not entry.is_file(follow_symlinks=False):
                                continue

                            # Filter extension BEFORE stat()
                            if not all_files:

                                extension = os.path.splitext(
                                    entry.name
                                )[1].lower()

                                if extension not in extensions:
                                    continue

                            # Windows stat
                            stat = entry.stat(
                                follow_symlinks=False
                            )

                            # Windows creation time
                            created_timestamp = stat.st_ctime

                            created_date = datetime.fromtimestamp(
                                created_timestamp
                            ).date()

                           

                            if date_from <= created_date <= date_to:
                               
                                files.append({
                                    "path": entry.path,
                                    "created": created_timestamp,
                                    "folder" : os.path.dirname(entry.path)
                                })

                        except (
                            PermissionError,
                            FileNotFoundError,
                            OSError
                        ):
                            continue

            except (
                PermissionError,
                FileNotFoundError,
                OSError
            ):
                pass

        scan(self.path)

        return files