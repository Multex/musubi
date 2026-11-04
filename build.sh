#!/bin/sh
# Builds the APK with the SDK tools directly, so there is no Gradle to match to a JDK.
set -eu
cd "$(dirname "$0")/android"

SDK=${ANDROID_HOME:-$HOME/Android/Sdk}
TOOLS=$SDK/build-tools/34.0.0
JAR=$SDK/platforms/android-28/android.jar
# d8 crashes on newer JDKs (seen on 27), so this must point at Java 17.
export JAVA_HOME=${JAVA_HOME:-/usr/lib/jvm/java-17-temurin}
export PATH="$JAVA_HOME/bin:$PATH"

rm -rf build
mkdir -p build/classes build/test
javac -d build/test ButtonMap.java test/ButtonMapTest.java
java -ea -cp build/test padbridge.app.ButtonMapTest
javac -source 8 -target 8 -Xlint:-options -cp "$JAR" -d build/classes ./*.java
"$TOOLS/d8" --lib "$JAR" --min-api 21 --output build build/classes/padbridge/app/*.class
"$TOOLS/aapt2" compile --dir res -o build/res.zip
"$TOOLS/aapt2" link -I "$JAR" --manifest AndroidManifest.xml \
    --min-sdk-version 21 --target-sdk-version 28 -o build/unsigned.apk build/res.zip
(cd build && "$TOOLS/aapt" add unsigned.apk classes.dex >/dev/null)
"$TOOLS/zipalign" -f 4 build/unsigned.apk build/aligned.apk

# Kept outside build/ because Android refuses updates signed with a different key.
[ -f debug.keystore ] || keytool -genkeypair -keystore debug.keystore -storepass android \
    -alias debug -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=padbridge"
"$TOOLS/apksigner" sign --ks debug.keystore --ks-pass pass:android --out build/padbridge.apk build/aligned.apk
echo "android/build/padbridge.apk"
