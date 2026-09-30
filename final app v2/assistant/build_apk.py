import os
import sys
import subprocess
import shutil

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
JAVA_HOME = os.path.join(ROOT_DIR, "android_tools", "jdk-17")
SDK_DIR = os.path.join(ROOT_DIR, "android_tools", "sdk")
BUILD_TOOLS_DIR = os.path.join(SDK_DIR, "build-tools", "34.0.0")
PLATFORM_JAR = os.path.join(SDK_DIR, "platforms", "android-34", "android.jar")
KEYSTORE = os.path.join(ROOT_DIR, "android_tools", "debug.keystore")

AAPT2 = os.path.join(BUILD_TOOLS_DIR, "aapt2.exe")
D8 = os.path.join(BUILD_TOOLS_DIR, "d8.bat")
ZIPALIGN = os.path.join(BUILD_TOOLS_DIR, "zipalign.exe")
APKSIGNER = os.path.join(BUILD_TOOLS_DIR, "apksigner.bat")
JAVAC = os.path.join(JAVA_HOME, "bin", "javac.exe")
JAR = os.path.join(JAVA_HOME, "bin", "jar.exe")

APP_DIR = os.path.join(ROOT_DIR, "mobile_assistant_app")
BUILD_DIR = os.path.join(APP_DIR, "build")
GEN_DIR = os.path.join(APP_DIR, "gen")
OUTPUT_DIR = os.path.join(ROOT_DIR, "voice_assistant_app", "build")

os.makedirs(BUILD_DIR, exist_ok=True)
os.makedirs(GEN_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

env = os.environ.copy()
env["JAVA_HOME"] = JAVA_HOME
env["PATH"] = os.path.join(JAVA_HOME, "bin") + os.pathsep + env.get("PATH", "")

def run(cmd, desc):
    print(f"[*] {desc}...")
    res = subprocess.run(cmd, shell=True, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[!] FAILED: {desc}")
        print("STDOUT:", res.stdout)
        print("STDERR:", res.stderr)
        sys.exit(1)
    return res

def main():
    print("=" * 60)
    print("   BUILDING TEACHABLE VOICE ASSISTANT ANDROID APK")
    print("=" * 60)

    # 1. Compile Resources
    res_dir = os.path.join(APP_DIR, "res")
    compiled_res = os.path.join(BUILD_DIR, "compiled_res.zip")
    run(f'"{AAPT2}" compile --dir "{res_dir}" -o "{compiled_res}"', "Compiling Android resources")

    # 2. Link Resources & Generate R.java & Unaligned APK
    manifest = os.path.join(APP_DIR, "AndroidManifest.xml")
    assets = os.path.join(APP_DIR, "assets")
    unaligned_apk = os.path.join(BUILD_DIR, "app-unaligned.apk")
    run(
        f'"{AAPT2}" link -o "{unaligned_apk}" -I "{PLATFORM_JAR}" '
        f'--manifest "{manifest}" -A "{assets}" "{compiled_res}" '
        f'--java "{GEN_DIR}" --auto-add-overlay',
        "Linking APK and generating R.java"
    )

    # 3. Compile Java Source Files
    classes_dir = os.path.join(BUILD_DIR, "classes")
    if os.path.exists(classes_dir): shutil.rmtree(classes_dir)
    os.makedirs(classes_dir, exist_ok=True)

    src_main = os.path.join(APP_DIR, "src", "com", "assistant", "teachable", "MainActivity.java")
    src_r = os.path.join(GEN_DIR, "com", "assistant", "teachable", "R.java")
    run(
        f'"{JAVAC}" -cp "{PLATFORM_JAR};{GEN_DIR}" -d "{classes_dir}" "{src_main}" "{src_r}"',
        "Compiling Java sources"
    )

    # 4. Convert Class Files to Dalvik Executable (classes.dex)
    dex_dir = os.path.join(BUILD_DIR, "dex")
    if os.path.exists(dex_dir): shutil.rmtree(dex_dir)
    os.makedirs(dex_dir, exist_ok=True)

    class_files = []
    for root, _, files in os.walk(classes_dir):
        for f in files:
            if f.endswith(".class"):
                class_files.append(f'"{os.path.join(root, f)}"')

    run(
        f'"{D8}" --output "{dex_dir}" {" ".join(class_files)} --lib "{PLATFORM_JAR}"',
        "DEXing classes with D8"
    )

    # 5. Pack classes.dex into unaligned APK
    classes_dex = os.path.join(dex_dir, "classes.dex")
    run(
        f'"{JAR}" -uf "{unaligned_apk}" -C "{dex_dir}" classes.dex',
        "Adding classes.dex into APK package"
    )

    # 6. ZipAlign APK
    aligned_apk = os.path.join(BUILD_DIR, "app-aligned.apk")
    if os.path.exists(aligned_apk): os.remove(aligned_apk)
    run(
        f'"{ZIPALIGN}" -v -p 4 "{unaligned_apk}" "{aligned_apk}"',
        "ZipAligning APK package"
    )

    # 7. Sign APK with debug keystore
    final_apk = os.path.join(OUTPUT_DIR, "TeachableVoiceAssistant.apk")
    run(
        f'"{APKSIGNER}" sign --ks "{KEYSTORE}" --ks-pass pass:android '
        f'--key-pass pass:android --out "{final_apk}" "{aligned_apk}"',
        "Signing APK with debug key"
    )

    # 8. Verify APK
    verify_res = run(f'"{APKSIGNER}" verify -v "{final_apk}"', "Verifying signed APK")
    print(verify_res.stdout)

    size_mb = os.path.getsize(final_apk) / (1024 * 1024)
    root_apk = os.path.join(ROOT_DIR, "TeachableVoiceAssistant.apk")
    shutil.copy2(final_apk, root_apk)
    print("=" * 60)
    print(f" SUCCESS! APK BUILT & SIGNED SUCCESSFULLY")
    print(f" Location: {final_apk}")
    print(f" Root Copy: {root_apk}")
    print(f" Size: {size_mb:.2f} MB")
    print("=" * 60)

if __name__ == "__main__":
    main()
