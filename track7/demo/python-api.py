def get_progress(student):
    return {"completed": student["progress"]["completed"]}


if __name__ == "__main__":
    get_progress({"name": "Aarav"})
