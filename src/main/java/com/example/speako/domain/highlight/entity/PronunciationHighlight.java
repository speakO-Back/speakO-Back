package com.example.speako.domain.highlight.entity;

import com.example.speako.domain.script.entity.Script;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Entity
@Table(name = "pronunciation_highlights")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class PronunciationHighlight {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "highlight_id")
    private Long highlightId;

    // Script 엔티티와의 다대일(N:1) 연관관계 매핑
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "script_id", nullable = false)
    private Script script;

    @Column(name = "word", nullable = false, length = 100)
    private String word;

    @Column(name = "standard_pronunciation", nullable = false, length = 100)
    private String standardPronunciation;

    @Enumerated(EnumType.STRING)
    @Column(name = "category", nullable = false)
    private HighlightCategory category; // 아래에 정의한 Enum 사용

    @Column(name = "rule_desc", columnDefinition = "TEXT")
    private String ruleDesc;

    @Column(name = "position_start", nullable = false)
    private int positionStart;

    @Column(name = "position_end", nullable = false)
    private int positionEnd;

    @Builder
    public PronunciationHighlight(Script script, String word, String standardPronunciation,
                                  HighlightCategory category, String ruleDesc, int positionStart, int positionEnd) {
        this.script = script;
        this.word = word;
        this.standardPronunciation = standardPronunciation;
        this.category = category;
        this.ruleDesc = ruleDesc;
        this.positionStart = positionStart;
        this.positionEnd = positionEnd;
    }
}