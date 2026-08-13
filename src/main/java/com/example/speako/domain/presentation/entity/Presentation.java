package com.example.speako.domain.presentation.entity;

import com.example.speako.domain.user.entity.User;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.CreationTimestamp;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

@Entity
@Table(name = "presentations")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Presentation {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "presentation_id")
    private Long presentationId;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @Column(name = "file_name", nullable = false)
    private String fileName;

    @Enumerated(EnumType.STRING)
    @Column(name = "file_type", nullable = false)
    private FileType fileType;

    @Column(name = "file_size", nullable = false)
    private float fileSize;

    @Column(name = "slide_count", nullable = false)
    private int slideCount;

    // ✨ AI 서버 프로젝트 번호 필드 추가
    @Column(name = "ai_project_id")
    private Long aiProjectId;

    @Column(name = "topic", nullable = false)
    private String topic;

    @Column(name = "duration", nullable = false)
    private int duration;

    @Enumerated(EnumType.STRING)
    @Column(name = "tone", nullable = false)
    private Tone tone = Tone.formal;

    @Column(name = "guideline", columnDefinition = "TEXT")
    private String guideline;

    @Column(name = "file_url", nullable = false, length = 2083)
    private String fileUrl;

    @CreationTimestamp
    @Column(name = "uploaded_at", nullable = false, updatable = false)
    private LocalDateTime uploadedAt;

    @OneToMany(mappedBy = "presentation", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<Slide> slides = new ArrayList<>();

    public enum FileType {
        ppt, pdf
    }

    public enum Tone {
        formal, casual
    }

    @Builder
    public Presentation(User user, String fileName, FileType fileType, float fileSize,
                        int slideCount, Long aiProjectId, String topic, int duration,
                        Tone tone, String guideline, String fileUrl) {
        this.user = user;
        this.fileName = fileName;
        this.fileType = fileType;
        this.fileSize = fileSize;
        this.slideCount = slideCount;
        this.aiProjectId = aiProjectId;
        this.topic = topic;
        this.duration = duration;
        this.tone = tone != null ? tone : Tone.formal;
        this.guideline = guideline;
        this.fileUrl = fileUrl;
    }

    // ✨ 전체 재생성 시 변경된 설정값을 반영하기 위한 update 메서드들 추가
    public void updateDuration(int duration) {
        this.duration = duration;
    }

    public void updateTone(Tone tone) {
        this.tone = tone;
    }

    public void updateGuideline(String guideline) {
        this.guideline = guideline;
    }
}