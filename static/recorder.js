let mediaRecorder;
let recordedChunks = [];

const recordButton = document.getElementById('recordButton');
const stopButton = document.getElementById('stopButton');
const audioPlayback = document.getElementById('audioPlayback');

recordButton.addEventListener('click', async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream);

    mediaRecorder.start();
    recordedChunks = [];

    mediaRecorder.addEventListener('dataavailable', event => {
        if (event.data.size > 0) {
            recordedChunks.push(event.data);
        }
    });

    mediaRecorder.addEventListener('stop', async () => {
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
        });
    });

    recordButton.disabled = true;
    stopButton.disabled = false;
});

stopButton.addEventListener('click', () => {
    mediaRecorder.stop();
    recordButton.disabled = false;
    stopButton.disabled = true;
});