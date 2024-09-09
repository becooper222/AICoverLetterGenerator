document.addEventListener('DOMContentLoaded', function() {
    const fileInput = document.getElementById('resume');
    const fileLabel = document.querySelector('label[for="resume"]');

    fileInput.addEventListener('change', function(e) {
        if (e.target.files.length > 0) {
            fileLabel.textContent = `File selected: ${e.target.files[0].name}`;
        } else {
            fileLabel.textContent = 'Upload your Resume';
        }
    });
});
