import winpty, threading, time
p = winpty.PtyProcess.spawn(['cmd'])
def r():
    try:
        while True:
            chunk = p.read(4096)
            if not chunk: break
    except EOFError:
        print("EOFError")
    except Exception as e:
        print(f"Exception: {type(e).__name__} {e}")
    print("Thread finished")

t = threading.Thread(target=r)
t.start()
time.sleep(1.0)
print("Closing pywinpty")
try:
    p.close(force=True)
except Exception as e:
    print(f"Close Exception: {e}")
t.join()
