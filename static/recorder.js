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
        mediaRecorder = new MediaRecorder(stream);

        mediaRecorder.start();
        recordedChunks = [];
        startTimer();

        mediaRecorder.addEventListener('dataavailable', event => {
            if (event.data.size > 0) {
                recordedChunks.push(event.data);
            }
        });

        mediaRecorder.addEventListener('stop', async () => {
            stopTimer();

            const blob = new Blob(recordedChunks, { type: 'audio/webm' });
            audioPlayback.src = URL.createObjectURL(blob);

            const formData = new FormData();
            formData.append('audio_data', blob, 'recording.webm');

            await fetch('/upload', {
                method: 'POST',
                body: formData
            }).then(response => {
                if (response.ok) {
                    alert('アップロード成功！');
                } else {
                    alert('アップロード失敗...');
                }
            }).catch(err => {
                console.error("アップロードエラー", err);
                alert('通信エラーによりアップロード失敗');
            });
        });

        recordButton.disabled = true;
        stopButton.disabled = false;

    } catch (err) {
        console.error("マイク取得エラー:", err);
        alert("🎤 マイクの許可が必要です。ブラウザの設定を確認してください。");
    }
});

stopButton.addEventListener('click', () => {
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
        mediaRecorder.stop();
    }
    recordButton.disabled = false;
    stopButton.disabled = true;
});
