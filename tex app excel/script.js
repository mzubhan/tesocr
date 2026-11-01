const uploadArea = document.getElementById('uploadArea');
const fileInput = document.getElementById('fileInput');
const fileInfo = document.getElementById('fileInfo');
const fileName = document.getElementById('fileName');
const fileSize = document.getElementById('fileSize');
const removeFile = document.getElementById('removeFile');
const convertBtn = document.getElementById('convertBtn');
const saveBtn = document.getElementById('saveBtn');
const progressSection = document.getElementById('progressSection');
const progressFill = document.getElementById('progressFill');
const progressText = document.getElementById('progressText');
const savedList = document.getElementById('savedList');
const savedEmpty = document.getElementById('savedEmpty');
const clearSavedBtn = document.getElementById('clearSavedBtn');
const conversionModeRadios = document.querySelectorAll('input[name="conversionMode"]');

const STORAGE_KEYS = {
    savedFormData: 'excelFormulaRemover.savedFormData',
    lastConversionMode: 'excelFormulaRemover.lastConversionMode'
};
const MAX_SAVED_ENTRIES = 20;

let selectedFile = null;
let savedEntries = loadSavedEntries();

initializeSavedData();
updateActionButtonsState();

// Upload area click
uploadArea.addEventListener('click', () => {
    fileInput.click();
});

// Drag and drop handlers
uploadArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadArea.classList.add('drag-over');
});

uploadArea.addEventListener('dragleave', () => {
    uploadArea.classList.remove('drag-over');
});

uploadArea.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadArea.classList.remove('drag-over');

    const files = e.dataTransfer.files;
    if (files.length > 0) {
        handleFileSelect(files[0]);
    }
});

// File input change
fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        handleFileSelect(e.target.files[0]);
    }
});

// Remove file button
removeFile.addEventListener('click', () => {
    selectedFile = null;
    fileInput.value = '';
    uploadArea.style.display = 'block';
    fileInfo.style.display = 'none';
    updateActionButtonsState();
});

// Handle file selection
function handleFileSelect(file) {
    const validTypes = [
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.ms-excel'
    ];

    if (!validTypes.includes(file.type) && !file.name.match(/\.(xlsx|xls)$/)) {
        alert('❌ File must be Excel format (.xlsx or .xls)');
        return;
    }

    selectedFile = file;
    fileName.textContent = file.name;
    fileSize.textContent = formatFileSize(file.size);

    uploadArea.style.display = 'none';
    fileInfo.style.display = 'flex';
    updateActionButtonsState();
}

// Format file size
function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

// Save button click
saveBtn.addEventListener('click', () => {
    if (!selectedFile) return;

    const mode = getSelectedMode();
    const newEntry = {
        fileName: selectedFile.name,
        fileSize: formatFileSize(selectedFile.size),
        conversionMode: mode,
        savedAt: new Date().toISOString()
    };

    savedEntries.unshift(newEntry);
    if (savedEntries.length > MAX_SAVED_ENTRIES) {
        savedEntries = savedEntries.slice(0, MAX_SAVED_ENTRIES);
    }

    if (!persistSavedEntries()) return;

    saveLastConversionMode(mode);
    renderSavedEntries();
    alert('✅ Form data saved in this browser.');
});

// Conversion mode persistence
conversionModeRadios.forEach((radio) => {
    radio.addEventListener('change', () => {
        if (radio.checked) {
            saveLastConversionMode(radio.value);
        }
    });
});

// Remove single saved entry
savedList.addEventListener('click', (event) => {
    const deleteButton = event.target.closest('.btn-delete-saved');
    if (!deleteButton) return;

    const index = Number(deleteButton.dataset.index);
    if (Number.isNaN(index)) return;

    savedEntries.splice(index, 1);
    if (!persistSavedEntries()) return;
    renderSavedEntries();
});

// Clear all saved entries
clearSavedBtn.addEventListener('click', () => {
    if (savedEntries.length === 0) return;
    const confirmed = confirm('Delete all saved data?');
    if (!confirmed) return;

    savedEntries = [];
    if (!persistSavedEntries()) return;
    renderSavedEntries();
});

// Convert button click
convertBtn.addEventListener('click', async () => {
    if (!selectedFile) return;

    convertBtn.disabled = true;
    saveBtn.disabled = true;
    progressSection.style.display = 'block';
    updateProgress(0, 'Preparing upload...');

    try {
        await convertExcelFile(selectedFile);
    } catch (error) {
        alert('❌ An error occurred: ' + error.message);
        console.error(error);
    } finally {
        // Reset UI state
        updateActionButtonsState();
        progressSection.style.display = 'none';
        progressFill.style.width = '0%';
    }
});

// Main conversion function that sends the file to the Python backend
async function convertExcelFile(file) {
    updateProgress(10, 'Uploading file to server...');

    const formData = new FormData();
    formData.append('file', file);
    
    // Ambil mode konversi yang dipilih user
    const mode = document.querySelector('input[name="conversionMode"]:checked').value;
    formData.append('conversionMode', mode);
    
    // Make a POST request to the Python server's /convert endpoint
    const response = await fetch(new URL('convert', window.location.href), {
        method: 'POST',
        body: formData
    });

    if (response.ok) {
        updateProgress(70, 'Server is processing file...');
        const blob = await response.blob(); // Get the processed file as a Blob

        updateProgress(90, 'Preparing download file...');
        const originalName = file.name.replace(/\.(xlsx|xls)$/, '');
        // The server will always return .xlsx
        const newFileName = `${originalName} TR.xlsx`;

        // Create a temporary link to trigger the browser's download functionality
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = newFileName;
        document.body.appendChild(a);
        a.click();
        
        // Clean up the temporary link object
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        
        updateProgress(100, 'Done!');
        setTimeout(() => {
            alert('✅ File successfully converted and downloaded!');
        }, 300);

    } else {
        // If the server returned an error, display it to the user
        const errorText = await response.text();
        throw new Error(`Server error (${response.status}): ${errorText}`);
    }
}

// Update progress bar
function updateProgress(percent, text) {
    progressFill.style.width = percent + '%';
    progressText.textContent = text;
}

function updateActionButtonsState() {
    const hasFile = Boolean(selectedFile);
    convertBtn.disabled = !hasFile;
    saveBtn.disabled = !hasFile;
}

function getSelectedMode() {
    const selectedMode = document.querySelector('input[name="conversionMode"]:checked');
    return selectedMode ? selectedMode.value : 'external';
}

function saveLastConversionMode(mode) {
    try {
        localStorage.setItem(STORAGE_KEYS.lastConversionMode, mode);
    } catch (error) {
        console.error('Failed to save conversion mode:', error);
    }
}

function restoreLastConversionMode() {
    try {
        const mode = localStorage.getItem(STORAGE_KEYS.lastConversionMode);
        if (!mode || !['external', 'all'].includes(mode)) return;

        const modeRadio = document.querySelector(`input[name="conversionMode"][value="${mode}"]`);
        if (modeRadio) {
            modeRadio.checked = true;
        }
    } catch (error) {
        console.error('Failed to restore conversion mode:', error);
    }
}

function loadSavedEntries() {
    try {
        const raw = localStorage.getItem(STORAGE_KEYS.savedFormData);
        if (!raw) return [];

        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) return [];

        return parsed.filter((entry) => {
            return entry &&
                typeof entry.fileName === 'string' &&
                typeof entry.fileSize === 'string' &&
                typeof entry.conversionMode === 'string' &&
                typeof entry.savedAt === 'string';
        });
    } catch (error) {
        console.error('Failed to load saved data:', error);
        return [];
    }
}

function persistSavedEntries() {
    try {
        localStorage.setItem(STORAGE_KEYS.savedFormData, JSON.stringify(savedEntries));
        return true;
    } catch (error) {
        console.error('Failed to save data:', error);
        alert('❌ Failed to save data in browser storage.');
        return false;
    }
}

function renderSavedEntries() {
    savedList.innerHTML = '';

    if (savedEntries.length === 0) {
        savedEmpty.style.display = 'block';
        clearSavedBtn.disabled = true;
        return;
    }

    savedEmpty.style.display = 'none';
    clearSavedBtn.disabled = false;

    savedEntries.forEach((entry, index) => {
        const modeLabel = entry.conversionMode === 'all'
            ? 'Remove ALL Formulas'
            : 'Remove External Links & Other Sheets';

        const listItem = document.createElement('li');
        listItem.className = 'saved-item';
        listItem.innerHTML = `
            <div class="saved-item-main">
                <span class="saved-item-title">${escapeHtml(entry.fileName)}</span>
                <span class="saved-item-meta">${escapeHtml(entry.fileSize)} • ${modeLabel} • ${escapeHtml(formatSavedAt(entry.savedAt))}</span>
            </div>
            <button class="btn-delete-saved" type="button" data-index="${index}">Delete</button>
        `;
        savedList.appendChild(listItem);
    });
}

function formatSavedAt(isoDate) {
    const date = new Date(isoDate);
    if (Number.isNaN(date.getTime())) return 'Unknown time';
    return date.toLocaleString();
}

function escapeHtml(value) {
    return value
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function initializeSavedData() {
    restoreLastConversionMode();
    renderSavedEntries();
}
