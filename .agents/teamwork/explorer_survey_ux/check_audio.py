import av

container = av.open(r'D:\Milind\Voice Assistant\External References\Toggle Bar Comparision.mp4')
print("Streams in Toggle Bar Comparision.mp4:")
for s in container.streams:
    print(f" - {s.type}: {s.name}, {getattr(s, 'codec_context', None)}")
if len(container.streams.audio) > 0:
    print("Has audio stream!")
else:
    print("No audio stream.")
