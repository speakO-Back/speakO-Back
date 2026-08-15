package com.example.speako.domain.presentation.entity;

import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import java.time.LocalDateTime;

@Entity
@Table(name = "download_logs")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class DownloadLog {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "download_id")
    private Long downloadId;

    @Column(name = "user_id", nullable = false)
    private Long userId;

    @Column(name = "presentation_id", nullable = false)
    private Long presentationId;

    @Enumerated(EnumType.STRING)
    @Column(name = "file_format", nullable = false)
    private FileFormat fileFormat;

    @Column(name = "includes_coaching", nullable = false)
    private boolean includesCoaching;

    @Column(name = "file_name", nullable = false)
    private String fileName;

    @Column(name = "downloaded_at", nullable = false)
    private LocalDateTime downloadedAt = LocalDateTime.now();

    @Builder
    public DownloadLog(Long userId, Long presentationId, FileFormat fileFormat, boolean includesCoaching, String fileName) {
        this.userId = userId;
        this.presentationId = presentationId;
        this.fileFormat = fileFormat; // 💡 타입이 일치하여 에러 해결!
        this.includesCoaching = includesCoaching;
        this.fileName = fileName;
    }
}