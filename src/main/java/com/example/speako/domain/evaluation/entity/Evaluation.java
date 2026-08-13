package com.example.speako.domain.evaluation.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import java.time.LocalDateTime;

@Entity
@Table(name = "evaluations")
@Getter
@Builder
@AllArgsConstructor
@NoArgsConstructor
public class Evaluation {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long evaluationId;

    private Long userId;
    private Long slideId;
    private Long recordingId;

    private String audioFileName;
    private Integer audioDuration;

    private Float totalScore;
    private Float pronunciationScore;

    private Integer fillerWordCount;

    @Column(columnDefinition = "json")
    private String fillerWordDetail;

    private Float pauseScore;

    @Column(columnDefinition = "json")
    private String pauseDetail;

    @Column(columnDefinition = "TEXT")
    private String recognizedText;

    @Column(columnDefinition = "TEXT")
    private String feedbackDetail;

    private LocalDateTime evaluatedAt = LocalDateTime.now();
    @PrePersist
    void onCreate() {
        if (this.evaluatedAt == null) this.evaluatedAt = LocalDateTime.now();
        if (this.fillerWordCount == null) this.fillerWordCount = 0;
        if (this.pauseScore == null) this.pauseScore = 0f;
        if (this.fillerWordDetail == null) this.fillerWordDetail = "[]";
        if (this.pauseDetail == null) this.pauseDetail = "[]";
    }
}