let mediaRecorder;
let recordedChunks = [];

const recordButton = document.getElementById('recordButton');
const stopButton = document.getElementById('stopButton');
const audioPlayback = document.getElementById('audioPlayback');

// 🔽 録音時間の表示（任意）
let timerInterval;
let seconds = 0;

function startTimer() {
    seconds = 0;
    timerInterval = setInterval(() => {
        seconds++;
        audioPlayback.innerText = `録音中: ${seconds}秒`;
    }, 1000);
}

function stopTimer() {
    clearInterval(timerInterval);
    audioPlayback.innerText = "";
}

recordButton.addEventListener('click', async () => {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        console.log("✅ マイクアクセス成功");

        mediaRecorder = new MediaRecorder(stream);
        recordedChunks = [];

        mediaRecorder.addEventListener('dataavailable', event => {
            if (event.data.size > 0) {
                recordedChunks.push(event.data);
                console.log("🎙️ 録音データ取得");
            }
        });

        mediaRecorder.addEventListener('stop', async () => {
            const blob = new Blob(recordedChunks, { type: 'audio/webm' });

            if (blob.size === 0) {
                alert("⚠️ 録音できていません。マイクを確認してください。");
                return;
            }

            audioPlayback.src = URL.createObjectURL(blob);
            audioPlayback.load();
            console.log("🎧 再生用URL生成");

            const formData = new FormData();
            formData.append('audio_data', blob, 'recording.webm');

            const response = await fetch('/upload', {
                method: 'POST',
                body: formData
            });

            if (response.ok) {
                alert('✅ アップロード成功！');
            } else {
                const err = await response.text();
                alert('❌ アップロード失敗: ' + err);
            }
        });

        mediaRecorder.start();
        console.log("🔴 録音スタート");
        startTimer();  // ← ここを追加！

        recordButton.disabled = true;
        stopButton.disabled = false;

    } catch (err) {
        console.error("❌ マイクの取得に失敗:", err);
        alert("マイクが使えません。ブラウザの設定をご確認ください。");
    }
});

stopButton.addEventListener('click', () => {
    if (mediaRecorder && mediaRecorder.state === "recording") {
        mediaRecorder.stop();
        console.log("🛑 録音ストップ");
        stopTimer();  // ← ここを追加！

        recordButton.disabled = false;
        stopButton.disabled = true;
    }
});
