let mediaRecorder;
let recordedChunks = [];

const recordButton = document.getElementById('recordButton');
const stopButton = document.getElementById('stopButton');
const uploadButton = document.getElementById('uploadButton');
const audioPlayback = document.getElementById('audioPlayback');

// タイマー用
let timerInterval;
let autoStopTimer = null;
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

        mediaRecorder = new MediaRecorder(stream, {
            mimeType: 'audio/webm;codecs=opus',
            audioBitsPerSecond: 128000
        });
        recordedChunks = [];

        // ✅ マイクの物理切断を検知
        stream.getAudioTracks()[0].onended = () => {
            alert("⚠️ マイクが途中で切断されました。");
            stopTimer();
            recordButton.disabled = false;
            stopButton.disabled = true;
        };

        mediaRecorder.addEventListener('dataavailable', event => {
            if (event.data.size > 0) {
                recordedChunks.push(event.data);
                console.log("🎙️ 録音データ取得");
            }
        });

        mediaRecorder.addEventListener('stop', () => {
            const blob = new Blob(recordedChunks, { type: 'audio/webm' });

            if (blob.size === 0) {
                alert("⚠️ 録音できていません。マイクを確認してください。");
                return;
            }

            audioPlayback.src = URL.createObjectURL(blob);
            audioPlayback.load();
            console.log("🎧 再生用URL生成");

            uploadButton.disabled = false;
            uploadButton.blob = blob;
        });

        mediaRecorder.start();
        console.log("🔴 録音スタート");
        startTimer();

        // ✅ 自動停止（60秒で）
        autoStopTimer = setTimeout(() => {
            if (mediaRecorder.state === "recording") {
                mediaRecorder.stop();
                console.log("🕒 自動停止");
                stopTimer();
                recordButton.disabled = false;
                stopButton.disabled = true;
            }
        }, 60000);

        recordButton.disabled = true;
        stopButton.disabled = false;
    } catch (err) {
        console.error("❌ マイクの取得に失敗:", err);
        alert("マイクが使えません。ブラウザの設定をご確認ください。");
    }
});

stopButton.addEventListener('click', () => {
    if (mediaRecorder && mediaRecorder.state === "recording") {
        clearTimeout(autoStopTimer);
        mediaRecorder.stop();
        console.log("🛑 手動停止");
        stopTimer();
        recordButton.disabled = false;
        stopButton.disabled = true;
    }
});

uploadButton.addEventListener('click', async () => {
  const blob = uploadButton.blob;
  if (!blob) {
    alert("録音データがありません");
    return;
  }

  // 1) サーバーへ送信 → job_id を取得
  const formData = new FormData();
  formData.append('audio_data', blob, 'recording.webm');

  let res;
  try {
    res = await fetch('/api/upload', { method: 'POST', body: formData });
  } catch (err) {
    alert("❌ ネットワークエラー: " + err);
    return;
  }
  if (!res.ok) {
    const text = await res.text();
    alert("❌ アップロード失敗: " + text);
    return;
  }
  const { job_id } = await res.json();
  if (!job_id) {
    alert("❌ サーバーから job_id が取得できません");
    return;
  }

  // 2) ポーリング開始（1.5秒ごと）
  const poll = setInterval(async () => {
    let statusRes, statusJ;
    try {
      statusRes = await fetch(`/api/job_status/${job_id}`);
      statusJ   = await statusRes.json();
    } catch (err) {
      console.error("ポーリングエラー", err);
      return;
    }

    if (statusJ.status === 'finished') {
      clearInterval(poll);
      alert(`✅ 詳細解析完了！スコア：${statusJ.score} 点`);
      window.location.href = '/dashboard';
    }
    else if (statusJ.status === 'failed') {
      clearInterval(poll);
      alert('❌ 詳細解析に失敗しました。');
      window.location.href = '/dashboard';
    }
    // 'running' の間は何もしない
  }, 1500);
});
