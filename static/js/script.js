document.addEventListener('DOMContentLoaded', function() {
    const fileInput = document.getElementById('resume');
    const fileLabel = document.querySelector('label[for="resume"]');
    const pdfPreview = document.getElementById('pdf-preview');
    const pdfContainer = document.getElementById('pdf-container');

    fileInput.addEventListener('change', function(e) {
        if (e.target.files.length > 0) {
            const file = e.target.files[0];
            fileLabel.textContent = `File selected: ${file.name}`;
            
            if (file.type === 'application/pdf') {
                const reader = new FileReader();
                reader.onload = function(e) {
                    const typedarray = new Uint8Array(e.target.result);
                    renderPdf(typedarray);
                };
                reader.readAsArrayBuffer(file);
            } else {
                pdfPreview.classList.add('hidden');
            }
        } else {
            fileLabel.textContent = 'Upload your Resume';
            pdfPreview.classList.add('hidden');
        }
    });

    function renderPdf(typedarray) {
        pdfjsLib.getDocument(typedarray).promise.then(function(pdf) {
            pdf.getPage(1).then(function(page) {
                const scale = 1.5;
                const viewport = page.getViewport({ scale: scale });
                const canvas = document.createElement('canvas');
                const context = canvas.getContext('2d');
                canvas.height = viewport.height;
                canvas.width = viewport.width;

                const renderContext = {
                    canvasContext: context,
                    viewport: viewport
                };

                page.render(renderContext);
                pdfContainer.innerHTML = '';
                pdfContainer.appendChild(canvas);
                pdfPreview.classList.remove('hidden');
            });
        });
    }
});
