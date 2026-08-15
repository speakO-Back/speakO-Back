package com.example.speako.domain.script.entity;

import com.example.speako.domain.highlight.entity.PronunciationHighlight;
import com.example.speako.domain.presentation.entity.Slide;
import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

@Entity
@Table(name = "scripts")
@Getter
@Builder
@AllArgsConstructor
@NoArgsConstructor
public class Script {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long scriptId;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "slide_id", nullable = false)
    private Slide slide;

    @Column(columnDefinition = "TEXT")
    private String content;

    // 빌더 사용 시 null이 들어가도 기본값이 깨지지 않게 설정
    @Builder.Default
    private Integer version = 1;

    @Enumerated(EnumType.STRING)
    @Builder.Default
    private RegenType regenType = RegenType.init;

    @Column(columnDefinition = "TEXT")
    private String regenRequest;

    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;

    public enum RegenType { init, full, partial }

    //  Script 객체에서 바로 연결된 하이라이트 목록을 조회가능
    @Builder.Default
    @OneToMany(mappedBy = "script", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<PronunciationHighlight> pronunciationHighlights = new ArrayList<>();

    @PrePersist
    void prePersist() {
        LocalDateTime now = LocalDateTime.now();
        if (createdAt == null) createdAt = now;
        if (updatedAt == null) updatedAt = now;
        if (version == null) version = 1;
        if (regenType == null) regenType = RegenType.init;
    }

    @PreUpdate
    void preUpdate() {
        updatedAt = LocalDateTime.now();
    }
    /** 재생성 결과로 대본을 갈아끼운다. version을 올려 몇 번째 재생성인지 남긴다. */
    public void updateContent(String newContent) {
        this.content = newContent;
        this.version = (this.version == null ? 1 : this.version + 1);
        this.regenType = RegenType.partial;
        this.updatedAt = java.time.LocalDateTime.now();
    }
}