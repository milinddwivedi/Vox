from PIL import Image

def make_thumb(src, dst):
    img = Image.open(src)
    w, h = img.size
    img.resize((w // 4, h // 4)).save(dst)
    print("Saved", dst)

make_thumb(r"D:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\frames_1031_020100\frame_013548.jpg", r"D:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\m1031_13548_thumb.png")
make_thumb(r"D:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\frames_1031_020100\frame_018064.jpg", r"D:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\m1031_18064_thumb.png")
make_thumb(r"D:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\frames_1031_020100\frame_022580.jpg", r"D:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\m1031_22580_thumb.png")
