# Bounce Studio

Buka `index.html` di Chrome atau Edge. Tidak perlu instalasi atau server.

1. Upload beberapa foto dan/atau video sekaligus (misalnya MP4 atau WebM yang didukung browser).
2. Pilih ukuran canvas dalam piksel dan kualitas video, kecepatan (piksel/detik), lebar foto, dan warna background.
3. Gunakan tombol jeda atau Acak ulang untuk mengatur preview.
4. Pilih durasi, lalu klik Ekspor MP4. Tetap buka tab sampai selesai.

Foto/video berganti berurutan setiap memantul dan kembali ke media pertama. Klik Acak urutan di bawah koleksi untuk mengacak urutan foto dan video; nomor thumbnail menunjukkan urutan preview dan ekspor. Posisi putar setiap video tetap tersimpan saat urutan diacak. Posisi dan arah awal diacak. Proporsi foto dipertahankan dan ukurannya dibatasi agar muat di canvas.

Ekspor merender frame demi frame menggunakan WebCodecs dan Mediabunny (vendor lokal, lisensi MPL-2.0), lalu menyusun MP4 H.264. Video 10 detik memiliki tepat 600 frame dengan timestamp tetap 1/60 detik. Ekspor tidak merekam preview dan tidak menunggu waktu nyata. Proses menunggu encoder agar tidak melewatkan frame. Browser harus mendukung WebCodecs dan encoding H.264 pada ukuran terpilih. Gunakan Chrome/Edge terbaru. Jika WebCodecs tidak tersedia melalui file lokal, jalankan `python3 -m http.server 8000` dari folder ini lalu buka http://localhost:8000.

Dimensi ekspor dibulatkan ke piksel genap agar sesuai encoder H.264. Foto diproses lokal, tidak ada audio. Font Google opsional memakai font sistem saat offline.

Aktifkan Background transparan untuk ekspor WebM VP9 dengan alpha (60 fps, frame demi frame). MP4 H.264 tetap dipakai saat opsi dimatikan. Transparansi membutuhkan dukungan encoder browser dan editor pemutar yang mendukung WebM alpha. Pilihan ini hanya menghapus background canvas, bukan background yang menyatu dengan foto. Pola kotak preview tidak ikut diekspor.

Ukuran canvas dipilih dalam piksel, termasuk preset 1080 × 1440 (3:4), atau ukuran sendiri 240–3840 piksel per sisi. Hasil ekspor memakai dimensi tersebut, dengan ukuran dibulatkan ke piksel genap untuk kompatibilitas encoder.

Kualitas encoding terpisah dari ukuran canvas: Hemat, Standar, Tinggi. Pilihan ini mengatur target bitrate berdasarkan jumlah piksel dan 60 fps, tanpa mengubah dimensi, komposisi, atau kecepatan. Panel menampilkan target Mbps dan perkiraan ukuran file berdasarkan durasi; ukuran aktual tergantung encoder dan konten.

Mode Maksimal (default) memakai quantizer 18 bila didukung encoder, dengan target bitrate lebih tinggi sebagai fallback. Ini bukan lossless. Render menggunakan resampling berkualitas tinggi dari gambar sumber. Opsi Batasi ke ukuran asli foto (default aktif) mencegah pembesaran di atas resolusi sumber; ukuran foto kecil mungkin lebih kecil dari lebar yang diminta. Nonaktifkan untuk memaksa lebar pilihan dengan potensi penurunan ketajaman.

Video diputar tanpa audio dan hanya maju selama tampil. Saat menyentuh tepi, media berganti sesuai urutan upload. Saat video muncul kembali, pemutaran lanjut dari posisi terakhir, bukan kembali ke awal. Setelah durasi sumber habis, video mengulang dari awal. Jeda preview juga menghentikan posisi video. Setiap ekspor dimulai dari media pertama dan posisi video nol; setelah selesai atau dibatalkan, posisi preview dipulihkan. Frame video dicari berdasarkan waktu sumber untuk menjaga hasil ekspor tetap konsisten pada 60 fps, terlepas dari kecepatan render.

Ekspor menggambar ulang background dan membatasi area gambar media setiap frame. Frame dikirim ke encoder sebagai salinan piksel RGBA dengan stride eksplisit, bukan referensi canvas yang berubah. Encoder software diprioritaskan jika didukung, dengan fallback ke pilihan browser; proses bisa lebih lambat.

Foto dinormalisasi menjadi raster dengan orientasi bawaan file saat browser mendukung `createImageBitmap`. Ukuran gambar dan area sumber gambar memakai dimensi raster yang sama, agar metadata orientasi tidak dipakai ulang saat menggambar ke canvas portrait.

## Edge Colors

Buka tab **Edge Colors** di navigasi atas, atau `edge.html`. Upload satu foto/video, pilih sisi kiri, kanan, atas, atau bawah, lalu tentukan ukuran canvas dan kualitas. Satu kolom piksel dari sisi kiri/kanan dibentangkan menjadi garis horizontal; satu baris dari sisi atas/bawah menjadi garis vertikal. Hasil hanya berisi garis warna, tanpa foto sumber.

Ekspor gambar ke PNG (lossless) atau JPG (kualitas kompresi dapat dipilih). Untuk sumber video, garis mengikuti perubahan warna tiap frame. Ekspor MP4 memakai 60 fps dengan durasi 1–60 detik; sumber mengulang bila lebih pendek, tanpa audio. PNG/JPG dari video menyimpan efek pada frame preview yang dipilih. Ekspor memakai ukuran canvas penuh meskipun preview ditampilkan lebih kecil.

Background Bounce bisa memakai foto/video sendiri melalui **Background sendiri**. Pilih Penuhi canvas (cover, tepi dipangkas) atau Tampilkan utuh (contain, ruang kosong mengikuti warna latar). Video background mengulang tanpa audio, berhenti saat preview dijeda, dan dimulai dari awal untuk setiap ekspor. Mode transparan mengabaikan background custom. Tombol Hapus background mengembalikan warna latar biasa.

Lebar media tidak lagi dibatasi 640 px: slider sampai 3840 px dan kolom angka bisa diisi lebih besar. 640 px hanya saran. Media tetap mempertahankan proporsi dan dibatasi agar muat di canvas; opsi Batasi ke ukuran asli media masih berlaku.
