import winpty, threading, time
p = winpty.PtyProcess.spawn(['python', '-c', 'import sys; print("hello"); sys.stdout.close(); time.sleep(1)'])
chunks = []
def r():
    try:
        while True:
            c = p.read(4096)
            if not c: break
            chunks.append(c)
    except EOFError:
        pass
t = threading.Thread(target=r)
t.start()
t.join() # waits for EOF
alive = p.isalive()
print(f"Alive after EOF: {alive}")
p.close(force=True)
