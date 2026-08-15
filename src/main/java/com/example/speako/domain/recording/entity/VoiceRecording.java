package com.example.speako.domain.recording.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import java.time.LocalDateTime;

@Entity
@Table(name = "voice_recordings")
@Getter
@Builder
@AllArgsConstructor
@NoArgsConstructor
public class VoiceRecording {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long recordingId;

    private Long presentationId;
    private Long userId;

    private String audioFileUrl;
    private Integer duration;

    @Builder.Default
    private LocalDateTime recordedAt = LocalDateTime.now();
}